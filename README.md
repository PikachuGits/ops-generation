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

命令从仓库根目录（本项目即仓库根目录）执行。先创建环境并安装依赖：

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

默认完整测试只用内存 SQLite、FakeRedis 和 mock adapter，不要求启动 PostgreSQL/Redis；`tests/test_local_e2e.py` 的两条 E2E 用例在未设置 E2E 环境变量时会跳过：

```bash
.venv/bin/python -m pytest -q --cov=app --cov-report=term-missing
```

如需执行真实本地 PostgreSQL 16/Redis 7 的端到端测试，先启动服务并迁移 schema。仅使用本地隔离测试数据库；用例会写入带随机标识的验收数据且不会自动清理，不要连接生产或共享数据库：

```bash
docker compose up -d postgres redis
.venv/bin/python -m alembic upgrade head
E2E_DATABASE_URL=postgresql+psycopg://ops:ops@127.0.0.1:5432/ops_generation \
E2E_REDIS_URL=redis://127.0.0.1:6379/0 \
.venv/bin/python -m pytest -q --cov=app --cov-report=term-missing
```

- `tests/test_pipeline.py`：mock 网关正常生成后断言 `drafted`、非空 `draft_id`、`lint_issues` 持久化；复核队列重试复用已保存的生成检查点。
- `tests/test_http_adapters.py`：真实 HTTP 适配器逻辑的 mock 响应测试覆盖 429 → `QUOTA_EXCEEDED`、5xx → `ALL_PROVIDERS_FAILED` 及响应 schema 错误；`tests/test_error_map.py` 断言失败状态、四类错误码和中文提示。
- `tests/test_idempotency.py`：相同组织、站点、用户、功能和规范化输入在 60 秒内复用同一 job；改变组织、站点或用户时分别新建 job；另测 Redis 不可用时数据库兜底及 TTL 到期。
- `tests/test_local_e2e.py`：显式指定本地 PG/Redis 后，以 FastAPI 测试客户端跑真实数据库和 Redis、mock 网关/复核队列；覆盖重复提交、8 路并发幂等、租户隔离、异步处理和 429 错误映射。测试只写入随机标识数据，不会自动清理。
- `tests/test_egress_guard.py`：拦截 `socket`/`httpx`，只放行网关和复核队列适配器及固定路径；用例使用 SQLite 与 mock HTTP，不代表生产网络防火墙测试。
- `tests/test_worker.py`：worker 正常完成、queued 任务重试及耗尽上限。
- 四条任务验收映射：① mock 正常生成 → `drafted` 和 `draft_id`（管线测试 + 本地 PG/Redis API 集成）；② HTTP adapter 的 mock 429 映射为 `QUOTA_EXCEEDED`，service 测试断言 `failed` 与中文提示，PG/Redis E2E 另用 mock 网关异常验证该状态持久化；③ 同一 scope 的 60 秒重复请求只生成同一 job（串行与 8 路并发测试，网关和复核各只调用一次）；④ 业务 HTTP 出站限制 → 固定两个 adapter 的 `socket`/`httpx` 白名单守卫及下方逐文件清单。

### 逐文件出站点清单

| 文件 | 可能的网络目标 | 范围与触发条件 |
|---|---|---|
| `app/clients/gateway.py` | 配置的 `GATEWAY_BASE_URL`，固定 `POST /api/gw/v1/chat/completions` | 唯一模型网关 HTTP adapter；仅网关启用且配置 URL 后调用；关闭环境代理读取。 |
| `app/clients/review_queue.py` | 配置的 `REVIEW_QUEUE_BASE_URL`，固定 `POST /api/review/drafts` | 唯一复核队列 HTTP adapter；随请求携带 `Idempotency-Key: job_id`；关闭环境代理读取。 |
| `app/db.py` | `DATABASE_URL` 指定的 PostgreSQL | SQLAlchemy 数据库连接；属于显式基础设施依赖，不是业务 HTTP 调用。 |
| `app/runtime.py` | `REDIS_URL` 指定的 Redis | 创建运行时 Redis client，并组装数据库、HTTP adapters 和 dispatcher。 |
| `app/queueing.py` | 通过 Celery task 进入 Redis broker | `CeleryDispatcher.enqueue()` 调用任务 `.delay()` 时触发。 |
| `app/worker.py` | `REDIS_URL` 指定的 Celery broker/backend | worker 领取任务、写入结果时使用；不访问第三方业务服务。 |
| `alembic/env.py` | `DATABASE_URL` 指定的 PostgreSQL | 仅运行 Alembic 迁移命令时连接数据库。 |
| `app/local_mock.py` | `DATABASE_URL` 与 `REDIS_URL`，且强制 loopback | 本地 mock 入口；网关和复核队列均为进程内 mock，不发外部 HTTP。 |

业务 HTTP 出站只有前两项：模型网关与复核队列。PostgreSQL/Redis/Celery 是独立基础设施网络连接，部署网络策略应仅放行配置的基础设施地址；`test_egress_guard.py` 使用本地 SQLite/mock，守卫覆盖范围不等同于生产网络 ACL。测试中的 `tests/conftest.py` 使用 ASGI in-process transport，`tests/test_http_adapters.py` 使用 `httpx.MockTransport`，不会访问真实业务服务。

### 本地复验与 CI 状态

- 按仓库 `requirements.txt` 在 Python 3.12.12 复跑默认全套：`33 passed, 2 skipped`；两条 skip 是未设置 E2E 环境变量时跳过的本地 PG/Redis 用例。设置 `E2E_DATABASE_URL` 与 `E2E_REDIS_URL` 后：`35 passed`。
- 交付验收记录的总覆盖率为 79%。本次在 Python 3.12.12 复核时，忽略分支计数的行覆盖率分别为默认 80%、启用 E2E 后 82%；按仓库 `pyproject.toml` 的 `branch = true` 统计时分别为 77%、78%。README 保留 79% 作为交付记录，并注明本次分支覆盖复测数，避免将不同覆盖率口径混为一个数字。
- CI 记录：工作流修正后的提交 `7cac139` 对应 [run #43999228171](https://github.com/PikachuGits/ops-generation/actions/runs/43999228171)，**通过**：`33 passed, 2 skipped`、分支覆盖率 77%、耗时 22s；两条 skip 是未设置 E2E 环境变量时跳过的本地 PG/Redis 用例，设置变量后本地为 `35 passed`。此后文档类提交（含本 README 更新）由同一工作流执行并保持通过。
- 工作流历史：首条 [run #43996080301](https://github.com/PikachuGits/ops-generation/actions/runs/43996080301)（SHA `15433b4a251df27ebb66c1e4a61d4770f5e99c28`）失败于安装依赖阶段，原因是工作流把仓库根误写成 `ops-generation/` 子目录，找不到 `ops-generation/requirements.txt`；该路径错误已由上述通过记录对应的提交修正。
- GitHub 仓库 `PikachuGits/ops-generation` 为私有仓库。审核人需由仓库所有者/管理员按其 GitHub 账号授予至少 Read 权限；未提供审核人账号，当前无法核验该具体人员的访问权。未经所有者明确授权不改变仓库可见性。

迁移：`alembic upgrade head`；降级命令会删除两张业务表，仅限可丢弃的本地数据库，执行前须自行备份并确认。

## 明确的对接边界

- 业务数据只来自调用方提供的 JSON 输入；测试只用虚构样例。不采集其他数据，也不调用未列明的外部数据工具。
- 外部 HTTP 仅调用配置的模型网关和复核队列；PostgreSQL、Redis 是明确的基础设施依赖，不属于业务外部数据源。幂等键不是裸 `input_sha256`：`input_sha256` 由 `function_code` 与规范化输入计算，最终幂等键还纳入 `org_id`、`site_id`、`user_id`、`function_code` 和 `input_sha256` 后再哈希；同一组织/站点/用户及功能下 60 秒内的相同输入复用同一 job，不同组织、站点或用户即使输入相同也互相隔离、分别建 job。
- 网关 URL 路径按需求约定 `/api/gw/v1/chat/completions`，复核队列按 `/api/review/drafts`；字段细节、鉴权和正式模板待依赖方提供接口契约后替换，现有适配器不含真实凭据。
- 不含前端、不实现用户注册登录/权限策略、不实现计费、不发送或发布内容；需由可信身份层提供租户上下文。
