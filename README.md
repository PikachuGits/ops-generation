# Ops Generation Service

运营辅助功能的独立后端底座：所有生成能力通过 `OpsGenerationService.run(function_code, inputs, user)` 进入同一条管线。它会创建任务、选择版本化演示模板、调用可注入模型网关、保存文案检查结果、提交复核草稿，成功终态为 `drafted`；本服务没有对外发布/发送步骤。

> 当前目录是独立交付骨架，不改动 ClawHunt 接单脚本或 webhook。`copywriting`、`proposal`、`meeting_summary` 及字段名是可替换的演示契约，不代表客户已经确认的 function code、模板或 schema。仅使用虚构测试数据。

## 架构与状态

- FastAPI：`POST /api/v1/generations` 是服务入口；`GET /api/v1/generations/{job_id}` 查询；`POST /api/v1/generations/{job_id}/retry` 重投仍为 queued 的任务。
- SQLAlchemy 2 + Alembic + PostgreSQL 16：`ops_generation_jobs` 存任务、输入快照、模板版本、生成检查点、错误码及 lint 结果；`ops_generation_idempotency` 为 Redis 不可用时提供事务级幂等兜底。查询默认排除软删除行，并按组织、站点、用户隔离。
- Redis：60 秒幂等 `SET NX` 快速抢占和处理租约；数据库事务/唯一键负责 Redis 不可用及并发首次写入时的兜底。
- Celery + Redis：`proposal`、`meeting_summary` 异步处理；`copywriting` 同步处理。worker late-ack、丢失重入及有上限的重试；生成结果在提交复核前先持久化，复核失败后重试不会再次调用模型。
- 仅 `app/clients/gateway.py` 和 `app/clients/review_queue.py` 实现 HTTP 出站；运行时 HTTP 客户端关闭环境代理读取。网关默认关闭，且没有真实密钥。复核草稿请求携带 `Idempotency-Key: job_id`；接收端须兑现该键才有重复请求去重保证。若接口不支持，不能宣称端到端 exactly-once。

## 错误码

| error_code | 面向用户的中文提示 |
|---|---|
| `QUOTA_EXCEEDED` | 模型额度已用尽，请稍后重试或联系管理员 |
| `SCHEMA_VALIDATION_FAILED` | 生成结果不符合约定格式，已记录待人工复核 |
| `ALL_PROVIDERS_FAILED` | 所有模型服务商均不可用，请稍后重试 |
| `GW_DISABLED` | 模型网关已关闭，请联系管理员开启 |
| 未知错误码 | 生成失败，请稍后重试 |

复核队列 503/超时不会把 job 标为 `drafted`，而是保留 `queued`；重投复用 job 与已保存的生成检查点。超过最大尝试次数后置 `failed`，使用已约定的 `ALL_PROVIDERS_FAILED` 码。

## 本地启动

需要 Docker Compose、Python 3.12+。命令均在本目录执行：

```bash
docker compose up -d postgres redis
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
set -a; source .env; set +a
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开终端并加载同一环境后启动异步 worker：

```bash
. .venv/bin/activate
set -a; source .env; set +a
celery -A app.worker:celery_app worker --loglevel=INFO
```

默认 `GATEWAY_ENABLED=false`、网关和复核队列 URL 为空，因此不会误调真实服务。接入时由部署方设置 `GATEWAY_ENABLED=true`、`GATEWAY_BASE_URL`、`REVIEW_QUEUE_BASE_URL`，并在接入前与依赖方确认实际请求/响应 schema、认证和重试契约；配置未就绪时不应开放业务流量。

数据库 `ops/ops` 和本地端口只用于开发。部署时必须替换口令、使用受控网络/密钥管理，不能把本地配置用于生产。

### 只用本地 mock 启动完整 HTTP API

迁移后可在本机运行实际 FastAPI HTTP 服务；这个入口强制 PostgreSQL 与 Redis 均为 loopback 地址，网关/复核队列均替换为本地桩，异步任务由本地后台线程驱动，不会访问客户或外部服务：

```bash
OPS_GENERATION_LOCAL_MOCK=1 \
DATABASE_URL=postgresql+psycopg://ops:ops@127.0.0.1:5432/ops_generation \
REDIS_URL=redis://127.0.0.1:6379/0 \
.venv/bin/uvicorn app.local_mock:app --host 127.0.0.1 --port 8000
```

本地模拟响应示例：

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/generations \
  -H 'Content-Type: application/json' \
  -H 'X-Org-ID: demo-org' -H 'X-Site-ID: demo-site' -H 'X-User-ID: demo-user' \
  -d '{"function_code":"copywriting","inputs":{"brief":"虚构本地活动"}}'
```

只允许在本机开发使用；不要把 `app.local_mock:app` 或 mock 响应部署到生产。

## API 演示

调用端必须经可信身份中间件，且由该中间件覆盖（不能透传客户端伪造的）`X-Org-ID`、`X-Site-ID`、`X-User-ID`。服务本身不实现登录认证，不可直接暴露到公网。

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/generations \
  -H 'Content-Type: application/json' \
  -H 'X-Org-ID: demo-org' -H 'X-Site-ID: demo-site' -H 'X-User-ID: demo-user' \
  -d '{"function_code":"copywriting","inputs":{"brief":"虚构演示活动"}}'
```

缺少身份上下文返回 401；空输入、缺参或未知演示 function code 返回 422。请勿在生产日志记录用户输入、生成内容或密钥。

## 验收与复验

```bash
pytest -q
pytest --cov=app --cov-report=term-missing
E2E_DATABASE_URL=postgresql+psycopg://ops:ops@127.0.0.1:5432/ops_generation \
E2E_REDIS_URL=redis://127.0.0.1:6379/0 pytest -q tests/test_local_e2e.py
```

- `tests/test_pipeline.py`：mock 网关 → `drafted`、`draft_id` 和 `lint_issues` 入库；验证复核重试复用生成检查点。
- `tests/test_error_map.py`、`tests/test_http_adapters.py`：429、5xx、响应 schema 错误与四类错误码中文提示。
- `tests/test_idempotency.py`：同输入三次仅一个 job、一次网关和复核调用；验证 Redis 缺失时数据库兜底、不同用户隔离和 60 秒过期。
- `tests/test_local_e2e.py`：显式指定本地 PG/Redis 后，以 FastAPI 测试客户端跑真实数据库与 Redis 的 mock 端到端；覆盖 API、同输入重复、并发幂等、租户隔离、异步处理和 429 错误映射。默认未设置 E2E 环境变量时会跳过。
- `tests/test_egress_guard.py`：拦截 `socket`/`httpx`，只放行网关和复核队列适配器及其固定路径。
- `tests/test_worker.py`：worker 正常完成、queued 任务重试与耗尽上限。
- 四条任务验收映射：① mock 正常生成 → `drafted` 和 `draft_id`（管线测试 + 本地 PG/Redis HTTP 集成）；② mock 429 → `failed`、`QUOTA_EXCEEDED` 和中文提示（HTTP adapter + DB/Redis 集成）；③ 同用户 60 秒重复请求 → 同一 job（串行与 8 路并发集成，网关和复核各只调用一次）；④ 出站限制 → 固定两个 adapter 的 socket/httpx 白名单守卫。
- `.github/workflows/tests.yml`：仓库连接 GitHub 后自动执行单测与覆盖率命令。当前工作区没有 Git 元数据/远端，因此未生成真实 CI 运行记录。
- 本轮本地验证：在真实本地 PG16/Redis7 上 `35 passed`，总覆盖率 81%；迁移版本 `0002_native_uuid_ids (head)`，PostgreSQL 回查确认 job 主键类型为 `uuid`；Uvicorn loopback mock server HTTP 验证健康、生成草稿、幂等重放、查询和跨用户 404 均通过。

迁移：`alembic upgrade head`；降级命令会删除两张业务表，仅限可丢弃的本地数据库，执行前须自行备份并确认。

## 明确的对接边界

- 业务数据只来自调用方提供的 JSON 输入；测试只用虚构样例。不采集其他数据，也不调用未列明的外部数据工具。
- 外部 HTTP 仅调用配置的模型网关和复核队列；PostgreSQL、Redis 是明确的基础设施依赖，不属于业务外部数据源。
- 网关 URL 路径按需求约定 `/api/gw/v1/chat/completions`，复核队列按 `/api/review/drafts`；字段细节、鉴权和正式模板待依赖方提供接口契约后替换，现有适配器不含真实凭据。
- 不含前端、不实现用户注册登录/权限策略、不实现计费、不发送或发布内容；需由可信身份层提供租户上下文。
