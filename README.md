# Riveretech Agent 工作区

多项目工作区：**每个项目一个目录**，目录名即项目名；项目内部自带 `backend/`、`frontend/`、
`deploy/`、`docs/` 等子目录，不再给子目录加项目前缀。

## 项目

| 目录 | 说明 |
|---|---|
| `personal-memory/` | 拾穗 · 个人信息管家（Vue 3 + FastAPI + LangChain + PostgreSQL/pgvector） |
| `ashen-crown/` | 灰烬王冠 · 游戏落地页（独立静态项目） |
| `goal_schemas/` | 目标 / 复盘相关的 schema（独立工作产物，当前未纳入 git 跟踪） |

## 目录约定

- 项目目录：根下每个项目一个文件夹，见名知意（如 `personal-memory/`）。
- 项目内部：`backend/`、`frontend/`、`deploy/`、`docs/`、`designs/`、`docker-compose.yml`、`.env`。
- 跨项目设施放在根目录：
  - `.git/` — 仓库
  - `.venv/` — 本地 Python 环境（当前供 `personal-memory/backend` 使用）
  - `.workbuddy/` — 工作区记忆与工具
  - `.gitignore` — 仓库级忽略规则
- 每个项目的启动方式见其自己的 `README.md`，例如 [`personal-memory/README.md`](personal-memory/README.md)。

## 快速开始（personal-memory）

```bash
cd personal-memory

# 后端（Windows）
cd backend && ../../.venv/Scripts/python.exe run_api.py

# worker（另开终端）
cd backend && ../../.venv/Scripts/python.exe -m workers.worker

# 前端（再开一个终端）
cd frontend && npm install && npm run dev
```

前端 <http://localhost:5173>，接口文档 <http://localhost:8000/api/docs>。
