#!/usr/bin/env bash
# AI PR Review 助手 一键部署脚本（在 VM 上执行）。
#
# 前提：仓库已 clone 到 /opt/pr-review，且 /opt/pr-review/.env 已填好 key。
# 用法：cd /opt/pr-review && bash deploy/deploy.sh
#
# 做四件事：拉最新代码 -> 构建前端 dist -> 构建并起后端容器 -> 安装 Caddy 站点配置并 reload。
# 全程不触碰主 Caddyfile 与另两项目（见复盘 D-10）。

set -euo pipefail

REPO=/opt/pr-review
CADDY_CONF=/etc/caddy/conf.d/pr.caddy

cd "$REPO"

echo "==> [1/4] 拉取最新代码 (main)"
git fetch origin -q
git checkout main -q
git pull -q origin main

echo "==> [2/4] 构建前端 dist"
cd "$REPO/frontend"
npm ci --no-audit --no-fund
npm run build
cd "$REPO"

echo "==> [3/4] 构建并启动后端容器"
if [ ! -f "$REPO/.env" ]; then
	echo "ERROR: $REPO/.env 不存在，请先填入 DEEPSEEK_API_KEY 等" >&2
	exit 1
fi
docker compose up -d --build
echo "    等待健康检查…"
sleep 6
docker compose ps

echo "==> [4/4] 安装 Caddy 站点配置并 reload"
sudo cp "$REPO/deploy/pr.caddy" "$CADDY_CONF"
# 先校验整体配置合法再 reload，避免拖垮另两项目
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy

echo "==> 完成。验证："
echo "    curl -s http://127.0.0.1:8080/api/health"
echo "    https://pr.qiniu.zdwktlj.top"
