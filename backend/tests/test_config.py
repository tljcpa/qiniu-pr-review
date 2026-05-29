"""配置加载测试：确保 .env 路径用绝对路径解析，不依赖 cwd（回归 L-03）。"""

from pathlib import Path

from app.config import _ENV_CANDIDATES, _REPO_ROOT, Settings


def test_env_candidates_are_absolute():
    # 所有候选 .env 路径必须是绝对路径，否则会随 cwd 漂移
    for path in _ENV_CANDIDATES:
        assert Path(path).is_absolute()


def test_repo_root_points_to_repo():
    # 仓库根下应能看到 backend 目录
    assert (_REPO_ROOT / "backend").is_dir()


def test_env_loads_from_explicit_file(tmp_path):
    # 显式给一个临时 .env，应被加载（验证机制本身有效）
    env_file = tmp_path / "custom.env"
    env_file.write_text("DEEPSEEK_API_KEY=test-key-123\n", encoding="utf-8")
    s = Settings(_env_file=str(env_file))
    assert s.deepseek_api_key == "test-key-123"


def test_cors_origin_list_wildcard():
    s = Settings(cors_origins="*")
    assert s.cors_origin_list() == ["*"]


def test_cors_origin_list_split():
    s = Settings(cors_origins="https://a.com, https://b.com")
    assert s.cors_origin_list() == ["https://a.com", "https://b.com"]
