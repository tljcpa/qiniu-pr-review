# 部署文档

本项目部署在 Azure VM（与另两个实训营项目共用一台机），通过共享的系统级 **Caddy** 提供 HTTPS。

- 在线地址：https://pr.qiniu.zdwktlj.top
- 后端端口：`127.0.0.1:8080`（仅回环，外部走 Caddy 443）
- 部署目录：`/opt/pr-review`

## 架构（同机三项目隔离）

```
              Internet :443
                  │
          ┌───────▼────────┐   系统级 Caddy
          │     Caddy      │   /etc/caddy/Caddyfile
          │ (自动 HTTPS)   │   import conf.d/*.caddy
          └───┬───┬───┬────┘
   pr.*       │   │   │      voice.*  publish.*
  ┌───────────▼┐ ┌▼──┐ ┌▼──────────┐
  │ pr-review  │ │voice│ │multi-publish│
  │ dist+8080  │ │8081 │ │ 8082       │
  └────────────┘ └────┘ └────────────┘
```

隔离手段（见复盘 D-10）：
- 独立 compose project name：`pr-review`
- 独立端口：`8080`（另两项目 8081 / 8082）
- 独立目录：`/opt/pr-review`
- 独立 Caddy 站点：`/etc/caddy/conf.d/pr.caddy`（不碰主 Caddyfile 和另两项目）

前端不进容器：`vite build` 产物 `frontend/dist` 由 Caddy `file_server` 直接托管，`/api/*` 反代到后端 8080。
SSE 进度流要求 Caddy 反代 `flush_interval -1` 关闭缓冲（见复盘 D-23）。

## 首次部署

```bash
# 1. clone（已 clone 可跳过）
sudo mkdir -p /opt/pr-review && sudo chown $USER /opt/pr-review
git clone https://github.com/tljcpa/qiniu-pr-review.git /opt/pr-review
cd /opt/pr-review

# 2. 配置环境变量（绝不入 git）
cp .env.example .env
vim .env          # 填 DEEPSEEK_API_KEY / AZURE_* / GH_TOKEN

# 3. 一键部署
bash deploy/deploy.sh
```

## 更新部署（已上线后）

```bash
cd /opt/pr-review && bash deploy/deploy.sh
```

脚本会：拉 main → `npm run build` 前端 → `docker compose up -d --build` 后端 →
拷贝 `deploy/pr.caddy` 到 conf.d → `caddy validate` 校验后 `systemctl reload caddy`。

## 验证

```bash
# 后端健康
curl -s http://127.0.0.1:8080/api/health        # {"status":"ok",...}
# 公网 HTTPS
curl -s https://pr.qiniu.zdwktlj.top/api/health
# 端到端：建任务 -> 看 SSE -> 取结果
curl -s -X POST https://pr.qiniu.zdwktlj.top/api/review \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://github.com/psf/requests/pull/7487"}'
```

## 排障

- **LLM 报 APIConnectionError**：多半是 `.env` 没被加载导致 key 为空（见复盘 L-03）。
  检查 `/opt/pr-review/.env` 存在且 key 非空：
  `docker compose exec backend python -c "from app.config import settings; print(len(settings.deepseek_api_key))"`
- **SSE 进度不实时**：确认 `pr.caddy` 的 `/api` 块有 `flush_interval -1`。
- **证书没签发**：确认 DNS `pr.qiniu.zdwktlj.top` A 记录指向本机公网 IP，且 80/443 NSG 放行。
- **改了配置怕影响另两项目**：`sudo caddy validate --config /etc/caddy/Caddyfile` 先校验再 reload。
