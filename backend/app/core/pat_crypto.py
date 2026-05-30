"""PAT 加密工具（AES-256-GCM）。

设计决策（见复盘 D-39）：
- 为什么加密存储而非 hash：PAT 需要被取出来使用（调 GitHub API），
  所以必须可逆加密（AES）而不是单向 hash（bcrypt）。
- 为什么选 AES-256-GCM：GCM 提供认证加密（AEAD），能检测数据篡改，
  比 AES-CBC 多一层完整性保护；256-bit key 是当前安全标准。
- nonce/IV：每次加密随机生成 12 字节 nonce（GCM 标准），nonce 前缀明文存储：
  存储格式 = hex(nonce) + ":" + hex(ciphertext + tag)
  解密时从头部取 nonce，再解密。nonce 不保密，只要不重用即可。
- master key：由 settings.pat_encrypt_key 提供（hex 编码 32 字节）；
  生产环境必须用 `openssl rand -hex 32` 生成并写入 .env。

绝对铁律：
- 明文 PAT 只出现在：收到用户请求的那一刻 + 调用 GitHub API 的那一刻
- 任何日志、响应、数据库字段中都只存加密态
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings


def _get_key() -> bytes:
    """从配置取 master key（hex → bytes，必须 32 字节）。

    pat_encrypt_key 未配置（即占位的 "00...00"）时：
    返回全零 32 字节——不安全，但至少加解密一致，让测试和本地开发能走通。
    生产部署必须在 .env 里用 `openssl rand -hex 32` 生成真实 key 覆盖。
    """
    raw = settings.pat_encrypt_key
    if not raw:
        return b"\x00" * 32
    key_bytes = bytes.fromhex(raw)
    if len(key_bytes) != 32:
        raise ValueError("pat_encrypt_key 必须是 hex 编码的 32 字节（64 个十六进制字符）")
    return key_bytes


def encrypt_pat(plain: str) -> str:
    """加密明文 PAT，返回 'nonce_hex:ciphertext_hex' 字符串（可直接存库）。"""
    key = _get_key()
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    # AESGCM.encrypt 返回 ciphertext + 16 字节 GCM tag 拼在一起
    ct_and_tag = aesgcm.encrypt(nonce, plain.encode("utf-8"), None)
    return f"{nonce.hex()}:{ct_and_tag.hex()}"


def decrypt_pat(encrypted: str) -> str:
    """解密 'nonce_hex:ciphertext_hex' 格式字符串，返回明文 PAT。

    解密失败（数据损坏/key 不对）抛 ValueError，上层按"PAT 不可用"处理。
    """
    try:
        nonce_hex, ct_hex = encrypted.split(":", 1)
        nonce = bytes.fromhex(nonce_hex)
        ct_and_tag = bytes.fromhex(ct_hex)
    except (ValueError, AttributeError) as exc:
        raise ValueError("加密 PAT 格式错误") from exc

    key = _get_key()
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ct_and_tag, None)
    except Exception as exc:
        raise ValueError("PAT 解密失败，可能 master key 已更换") from exc
    return plaintext.decode("utf-8")
