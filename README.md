# 拾穗 · 个人信息管家

把散落各处的想法、资料、待办记到一处，让它们自己长出线索。

- **记**：一句话、一段摘录、一个链接，直接丢进来，不等分析、不填表单。
- **整理**：自动分类、打标签、抽重点、切片段、建关联，全程后台完成。
- **找**：关键词 + 语义混合检索，搜"我上次说的那个召回"也能找到。
- **问**：基于你自己的记录回答，每条结论后面挂着能点开的原文。
- **回顾**：周/月总结，正文里的每个数字都能在统计原值里核对到；行动建议必须挂证据。

技术栈：**Vue 3 + TypeScript + Vite** / **FastAPI + Python 3.12** / **LangChain 1.x + LangGraph** / **PostgreSQL 16 + pgvector**。

---

## 1. 快速开始

### 方式一：Docker（推荐，一次起全部）

```bash
cp .env.example .env
# 编辑 .env，至少填 LLM_API_KEY（和 EMBEDDING_API_KEY，若供应商不同）

docker compose up -d --build
```

打开 <http://localhost:8080>，注册一个账号就能用。

第一次启动会依次做：建库和角色 → 建扩展 → Alembic 建表与 RLS → 起 API、两个 worker、前端。
`docker compose logs -f migrate` 能看到迁移过程。

### 方式二：本地开发

```bash
# 起数据库（只需要这一个容器）
docker compose up -d db

# 后端
cd backend
python -m venv ../.venv && ../.venv/bin/pip install -e ".[dev]"   # Windows: ../.venv/Scripts/pip
cp ../.env.example ../.env
../.venv/bin/alembic upgrade head
../.venv/bin/uvicorn app.main:app --reload --port 8000

# 另开一个终端：worker（不入库分析、不生成总结的话可以不起）
cd backend && ../.venv/bin/python -m workers.worker

# 再开一个终端：前端
cd frontend && npm install && npm run dev
```

前端 <http://localhost:5173>（已配好 `/api` 代理到 8000）。
接口文档 <http://localhost:8000/api/docs>。

> **注意**：本地直接连库时，如果用的是 `postgres` 超级用户，行级安全策略会被绕过。
> 想验证隔离，请用 `pim_app` 角色（`docker compose` 的 init 脚本已建好）。

---

## 2. 目录结构

```
.
├── backend/
│   ├── app/
│   │   ├── api/            # 路由：auth / items / search / ask / todos / digests / stats
│   │   ├── agents/
│   │   │   ├── ingest/     # 路径一：四步流水线（不用 Agent）
│   │   │   ├── ask/        # 路径二：唯一用 Agent 的地方，含 6 个中间件
│   │   │   └── digest/     # 路径三：总结，含数字校验
│   │   ├── infra/          # db / llm / retrieval / sse / textproc / tokens / security
│   │   ├── repo/           # 数据访问，全部接收「已设置 user_id 的连接」
│   │   └── schemas/        # Pydantic 出入参
│   ├── migrations/001_init.sql   # 建表 + RLS + 触发器（DDL 正文）
│   ├── alembic/                  # 迁移版本管理，加载上面那份 SQL
│   ├── workers/                  # 数据库轮询 worker
│   └── tests/{unit,integration}
├── frontend/src/
│   ├── api/{client,sse}.ts       # fetch 封装 + SSE 流式解析
│   ├── stores/                   # Pinia：auth / items / ask / todos
│   ├── views/                    # 记一笔 / 收件箱 / 问一句 / 待办 / 回顾
│   └── components/               # ItemCard / CitationBadge / MarkdownText
├── deploy/                       # Dockerfile / init SQL
├── designs/                      # 架构设计文档（为什么这么设计）
└── docker-compose.yml
```

---

## 3. 几个关键设计决定

完整论证在 `designs/个人信息管家-设计方案-v1.md`。这里只留结论和"为什么"。

### 3.1 不做成一个万能 Agent，而是三条路径

| 路径 | 形态 | 管什么 |
|---|---|---|
| 入库 | 固定四步流水线，无循环 | 规范化 → 分析 → 向量化 → 建关联 |
| 问答 | 单 Agent + 5 个工具，有界 5 轮 | 检索、追问、作答 |
| 总结 | 批处理 | 周/月回顾、待办整理 |

理由很直接：把"记一句话"塞进 Agent 循环，它就要等模型思考 2–4 秒，
同一段话每次分类结果还可能不同，而且出错时无法定位是哪一步。
Agent 的价值只出现在"需要多步检索、需要判断够不够"的地方——也就是问答。

### 3.2 LangChain 只用两层

- **用**模型接入层（`init_chat_model`、`with_structured_output`）：换供应商改一行。
- **用**Agent 层（`create_agent` + middleware）：六个拦截点的调用时机由框架保证。
- **不用**手写 `StateGraph`：`create_agent` 本身就跑在 LangGraph 上，手写等于把它的循环重写一遍。
- **不用** `PGVectorStore`：它会自建表、自建连接池，连接不经过我们的 `SET LOCAL app.user_id`，
  等于绕开行级安全策略。检索因此用几十行原生 SQL 自己写。

### 3.3 总结里的数字一个都不许模型写

模型只输出 `{{stat:itemCount}}` 这样的占位符，数字全部由 SQL 算出后程序替换。
校验规则是"去掉占位符后正文里不能有任何数字字符"——于是正文里出现的每个数字，
都必然来自某次 SQL 统计。这比"事后用例把数字对一遍"可靠得多。

### 3.4 推断与事实分开存

`items` 存用户原话（永不修改），`item_analyses` 存模型判断（带 `confidence` / `model` / `prompt_version`）。
用户手改过的记录置 `user_pinned=true`，模型此后再也不覆盖它。
推测不会因为"写进了数据库"就变成事实。

---

## 4. 安全：三个角色与行级安全

**这是最容易配了等于没配的地方。**

PostgreSQL 里，表 owner 和超级用户默认**绕过** RLS；超级用户连 `force row level security` 都管不住。
所以部署必须分三个角色：

| 角色 | 用途 | 关键点 |
|---|---|---|
| `pim_owner` | 跑迁移 | 表的 owner，`FORCE RLS` 对它同样生效 |
| `pim_app` | API 连接 | 非 owner、非超级用户，RLS 完整生效 |
| `pim_worker` | 后台任务 | 同上；`jobs` 表只有它能读 |

- 每次开事务都由 `Database.tx(user_id)` 注入 `set_config('app.user_id', ..., true)`（作用域仅当前事务）。
- 含租户数据的表全部 `enable + force`，逐表写策略；没有 `user_id` 列的子表用 `exists` 回溯父表。
- 手动 SQL 里**不写** `where user_id = ?` 也能保证不越权——RLS 兜底。写显式条件只是为了用上索引。
- `jobs` 是全局队列，没有租户列，改用权限收紧：`pim_app` 只能 `insert`，读不到别人投了什么。
- 登录/注册没法设置 `app.user_id`（此刻还不知道你是谁），因此只开了两个**只做一件事**的
  定义者函数 `auth_lookup` / `auth_register`，不给任何通用后门。

⚠️ **一个不报错的坑**：`FORCE RLS` 下写别人的数据不会抛错，只会命中 0 行。
所以 `repo` 层任何 `update` / `delete` 都会检查受影响行数，集成测试里也有一条专门断言"命中 0 行"。

---

## 5. 怎么验证它真的在工作

```bash
cd backend

# 单元测试（不需要数据库、不需要模型密钥）
../.venv/bin/python -m pytest tests/unit -q

# 数据库集成测试：验证跨用户读写被拦住
# 必须用非超级用户角色，否则测试会自我跳过（并告诉你原因）
TEST_DATABASE_URL=postgresql://pim_app:pim_app@localhost:5432/pim \
  ../.venv/bin/python -m pytest tests/integration -q
```

集成测试里最关键的一条是 `test_wrong_user_write_affects_zero_rows`：
它断言"跨用户写入受影响行数 = 0"，而不是"抛异常"——因为真实行为就是不抛异常。

启动时还有两道自检（`app/main.py`）：
- 连接角色是超级用户 → 开发环境告警、生产环境**拒绝启动**；
- 配了模型密钥就探一次 embedding 维度，和数据库的 `vector(N)` 对不上直接报错，
  而不是等到写库时抛一个看不懂的 pgvector 错误。

---

## 6. 配置

见 `.env.example`，全部有默认值。需要改的通常只有三个：

| 变量 | 说明 |
|---|---|
| `LLM_BASE_URL` / `LLM_API_KEY` | 任何 OpenAI 兼容网关都行（DeepSeek / 通义 / Moonshot / vLLM） |
| `EMBEDDING_DIM` | 必须与数据库 `vector(N)` 一致，默认 1024。**改这个等于重建全部向量** |
| `JWT_SECRET` | 生产必须换：`python -c "import secrets;print(secrets.token_urlsafe(48))"` |

`PROMPT_CACHE_ENABLED` 默认关。只有供应商要求显式标记（Anthropic 系）时才开；
OpenAI 兼容网关多按前缀自动命中，硬塞 `cache_control` 可能被拒。

预算相关（`MAX_ROUNDS` / `TURN_TOKENS` / `MAX_TOOL_CALLS`）改一个要顺手重算另两个，
它们的关系写在 `app/agents/ask/middleware/budget.py` 顶部。

---

## 7. 已知限制

1. **中文全文检索靠 jieba 预处理**。PostgreSQL 没有中文词典，`simple` 解析器会把整句当一个词元。
   所以 `items.search_text` / `chunks.search_text` 存的是分词结果，写入和查询两边都要分词。
2. **向量维度改了要重建**。`chunks.embedding` 是定长 `vector(N)`，换 embedding 模型需要
   改列类型并重算所有切片，没有做在线迁移。
3. **单机部署**。worker 用数据库轮询 + `for update skip locked`，够用且没有额外中间件；
   要横向扩很多 worker 时应换成专门的队列。
4. **没有多语言 UI**，也没有移动端专门适配（窄屏会折行，能用但不算精致）。
5. **数据库集成测试需要真实 PG + pgvector**，本地没有环境时会自动跳过，不算通过。

---

## 8. 下一步可以做的

- 图片/语音入口（`items.kind` 已留 `file`，缺上传与 OCR/ASR 管线）
- 关联的"疑难裁决"定期批处理（当前只做向量阈值，不调模型）
- 定期总结的自动触发（现在要手动点生成）
- 导出（Markdown / JSON）
