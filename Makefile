# ===== 开发便捷命令 =====
# 用法：make <target>

.PHONY: help install install-dev test dev run health compose-up compose-down

help:
	@echo "install      创建 venv 并装后端运行依赖"
	@echo "install-dev  创建 venv 并装运行+测试依赖（含 pytest）"
	@echo "test         装测试依赖并跑全部单元测试（85+，全离线不打网络）"
	@echo "dev          本地热重载启动后端 (uvicorn --reload)"
	@echo "run          本地启动后端（无 reload）"
	@echo "health       冒烟测试健康检查端点"
	@echo "compose-up   docker compose 起后端"
	@echo "compose-down docker compose 停"

install:
	python3 -m venv .venv
	.venv/bin/pip install -U pip
	.venv/bin/pip install -r backend/requirements.txt

install-dev:
	python3 -m venv .venv
	.venv/bin/pip install -U pip
	.venv/bin/pip install -r backend/requirements-dev.txt

test:
	test -d .venv || python3 -m venv .venv
	.venv/bin/pip install -q -r backend/requirements-dev.txt
	cd backend && ../.venv/bin/python -m pytest tests/ -q

dev:
	cd backend && ../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

run:
	cd backend && ../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080

health:
	curl -s http://localhost:8080/api/health

compose-up:
	docker compose up -d --build

compose-down:
	docker compose down
