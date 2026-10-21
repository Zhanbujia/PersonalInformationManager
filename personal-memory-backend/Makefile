SHELL := /bin/bash
PY := ../.venv/Scripts/python.exe
ifeq ($(OS),Windows_NT)
	PY := ../.venv/Scripts/python.exe
else
	PY := ../.venv/bin/python
endif

.PHONY: help install db-up db-down migrate verify-db test test-unit dev-api dev-worker dev-web build-web fmt

help:
	@echo "make install     安装后端与前端依赖"
	@echo "make db-up       起本地 PostgreSQL（pgvector）"
	@echo "make migrate     建表 + RLS 策略（用 pim_owner 身份）"
	@echo "make verify-db   核对迁移结果：表/索引/策略/RLS 是否真的生效"
	@echo "make dev-api     起后端（热重载）"
	@echo "make dev-worker  起后台 worker（入库 + 总结）"
	@echo "make dev-web     起前端（Vite）"
	@echo "make test        跑单元测试"
	@echo "make test-db     跑数据库集成测试（需要 DATABASE_URL 指向非超级用户）"

install:
	$(PY) -m pip install -e ".[dev]"
	cd ../personal-memory-frontend && npm install

db-up:
	cd .. && docker compose up -d db
	@echo "等 db healthy…"
	@cd .. && docker compose ps db

db-down:
	cd .. && docker compose down

migrate:
	$(PY) -m alembic upgrade head

verify-db:
	$(PY) -m scripts.verify_db

test:
	$(PY) -m pytest tests/unit -q

test-db:
	$(PY) -m pytest tests/integration -q

dev-api:
	$(PY) run_api.py

dev-worker:
	$(PY) -m workers.worker

dev-web:
	cd ../personal-memory-frontend && npm run dev

build-web:
	cd ../personal-memory-frontend && npm run build

fmt:
	$(PY) -m ruff check --fix . || true
