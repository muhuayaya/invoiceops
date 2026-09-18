# InvoiceOps

> 面向中英混合发票/付款工单的多标签分流与人工复核 PoC。

InvoiceOps 把工单接入、分类、风险判断、责任队列推荐、批次处理、人工复核、审计追踪、运营指标和管理员数据管理串成一个可运行的业务闭环。项目重点不只是一个文本分类模型，还包括权限、持久化、异步任务、恢复和 Docker Compose 部署。

## 功能概览

- 单条中英混合工单分类；
- UTF-8 CSV 批次处理、进度和逐行失败原因；
- 高风险/低置信度工单进入人工复核；
- 复核决策、修订历史和审计事件；
- 仅管理员可用的数据录入、筛选、单条/批量删除和批量覆盖；
- PostgreSQL 业务存储、Redis/Celery 批次队列和 MLflow 实验跟踪；
- /healthz、/readyz、/metrics 和 GitHub Actions 检查。

## 技术栈

| 层次 | 技术 |
| --- | --- |
| Web | React 19、TypeScript、Vite、Nginx |
| API | FastAPI、Pydantic、JWT |
| 业务与存储 | Python、领域服务、PostgreSQL、SQLite fallback |
| 异步任务 | Celery、Redis |
| 模型 | 线上关键词规则 fallback；离线 TF-IDF baseline 和 XLM-R |
| 交付 | Docker Compose、GitHub Actions、OpenSpec |

## 项目架构

InvoiceOps 采用“前端工作台 + FastAPI 业务 API + 领域服务 + 持久化/异步任务 + 可替换模型运行时”的分层架构。单条工单走同步 API，CSV 批次通过 Redis/Celery 投递给 worker；高风险或低置信度结果进入人工复核，复核、删除、覆盖和清理操作都会留下审计事件。

```text
┌──────────────────────────────────────────────────────────────┐
│ React + TypeScript 工作台                                    │
│ 单条分类 · 批次处理 · 人工复核 · 审计追踪 · 指标 · 数据管理     │
└───────────────────────────────┬──────────────────────────────┘
                                │ HTTP / JSON
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ FastAPI API 层                                                │
│ JWT 认证与角色权限 · Pydantic 契约 · 健康检查 · 指标接口        │
└───────────────┬──────────────────────┬───────────────────────┘
                │                      │
                ▼                      ▼
┌─────────────────────────┐  ┌─────────────────────────────────┐
│ 领域服务与模型运行时     │  │ 异步任务链路                     │
│ 脱敏 · 分类 · 风险判断   │  │ API → Redis → Celery worker      │
│ 路由 · 复核 · 审计        │  │ 批次进度、检查点与恢复             │
└──────────┬──────────────┘  └────────────────┬────────────────┘
           │                                  │
           └────────────────┬─────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 数据与基础设施                                                │
│ PostgreSQL 业务数据 · SQLite 开发 fallback · Redis 队列        │
│ Alembic 迁移 · MLflow 实验跟踪 · Nginx 静态资源                │
└──────────────────────────────────────────────────────────────┘
```

### 主要模块职责

| 模块 | 代码位置 | 作用 |
| --- | --- | --- |
| Web 工作台 | `apps/web/` | 提供中文运营界面、角色菜单、表单、批次进度和结果展示 |
| API 入口 | `apps/api/` | 暴露分类、批次、复核、审计、指标和管理员数据管理接口 |
| 领域层 | `src/invoiceops/` | 执行脱敏、分类、风险判断、路由、复核、审计和权限规则 |
| Worker | `apps/worker/`、`src/invoiceops/batch.py`、`src/invoiceops/batch_store.py` | 消费 Celery 批次任务，记录进度、失败原因和可恢复检查点 |
| 持久化 | `src/invoiceops/adapters/`、`src/invoiceops/batch_store.py` | 支持 PostgreSQL 主存储和无 `DATABASE_URL` 时的 SQLite fallback |
| 模型与评估 | `src/invoiceops/ml_runtime/`、`ml/` | 线上使用可解释关键词 fallback；离线保留 TF-IDF 与 XLM-R 训练评估链路 |
| 基础设施 | `infra/`、`docker-compose.yml` | 提供镜像、Nginx、数据库迁移和 Compose 服务编排 |

### 两条核心处理链路

1. **单条工单**：Web 提交文本 → API 认证与脱敏 → 模型预测 → 风险/路由决策 → 持久化预测、工单和审计事件 → 必要时进入人工复核。
2. **CSV 批次**：Web 上传 UTF-8 CSV → API 创建批次 → Redis/Celery 投递任务 → worker 逐行处理并记录成功/失败 → Web 轮询批次状态和逐行错误原因。

完整部署时，PostgreSQL、Redis、MLflow、API、worker 和 Web 由 Docker Compose 管理；`migrate` 服务在 API/worker 启动前执行数据库迁移。模型和数据边界见[模型卡](docs/model/model-card-v1.md)与[数据卡](docs/data/data-card-v1.md)。

## 5 分钟启动：本机 Docker

前置条件：Docker Engine/Docker Desktop、Docker Compose v2、Git 和浏览器。本机不需要单独安装 PostgreSQL 或 Redis，Compose 会启动项目自己的服务，并在 API/worker 启动前执行 Alembic 数据库迁移。

~~~powershell
git clone <你的 GitHub 仓库地址>
Set-Location <项目目录>
Copy-Item .env.example .env
docker compose config
docker compose up --build -d
docker compose ps
~~~

启动后访问：

- Web：<http://localhost:3000>
- API 文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/healthz>

Windows PowerShell 自检：

~~~powershell
curl.exe --fail http://localhost:8000/healthz
curl.exe --fail http://localhost:8000/readyz
curl.exe --fail http://localhost:3000/
~~~

默认演示账号为 admin/admin、reviewer/reviewer、observer/observer，仅在 development/test 环境启用，不能用于生产。

完整命令、虚拟机部署、数据卷和故障排查见[本机 Docker 部署指南](docs/deployment-local-docker.md)和[项目使用手册](docs/使用手册-v1.md)。

## 当前模型边界

当前 API/Compose serving 使用 keyword-baseline-0.1 确定性关键词分类器，不依赖 BERT、XLM-R 或其他预训练权重。项目同时保留：

- TF-IDF 字符 n-gram + One-vs-Rest Logistic Regression 离线基线；
- 基于 FacebookAI/xlm-roberta-base 的 XLM-R v1/v2/v3 离线训练和评估记录。

XLM-R v3 已在冻结的 240 条受控数据上完成离线评估，但模型权重未随仓库和 API 镜像发布，也没有进入线上 active runtime。不要把离线指标描述为真实企业生产效果。详见[模型卡](docs/model/model-card-v1.md)。

## 本地开发检查

~~~powershell
python -m pip install -r requirements.lock
npm ci --ignore-scripts
python -m pytest -q
ruff check apps src ml tests scripts
npm run check
npm run build
openspec validate --all --strict --json
~~~

不启动 Docker 时，可按[项目使用手册的分开启动方式](docs/使用手册-v1.md#9-本机开发方式api-与-web-分开启动)运行 API 和 Web；完整的 PostgreSQL、Redis、worker 行为应使用 Compose 验证。

## 目录结构

~~~text
apps/api/       FastAPI 入口和 HTTP 路由
apps/worker/    Celery worker 入口
apps/web/       React 页面、API 客户端和中文映射
src/invoiceops/ 领域服务、仓储、批次、权限和模型运行时
configs/        taxonomy、阈值、训练和 LLM 配置
infra/          Dockerfile、Nginx 和数据库迁移
ml/             数据、基线、XLM-R 训练与评估代码
tests/          单元、契约、集成和端到端测试
docs/           使用、部署、模型、安全和路线文档
openspec/       项目需求、设计、规格和任务记录
~~~

## 后续演进

项目下一步不是简单堆功能，而是按“真实数据 → 可复现评估 → XLM-R shadow serving → 灰度/回滚 → 企业身份与数据治理”的顺序推进。具体路线见[后续演进路线](docs/roadmap-v1.md)。

## 发布前检查

发布前请确认 `.env`、模型权重、`node_modules`、`.venv` 和本地数据卷均未被 Git 跟踪，并重新执行测试、前端构建和 OpenSpec 校验。

## 许可证与数据说明

当前项目是个人学习/面试展示用途的 PoC。示例工单和训练数据以合成、脱敏数据为主；公开数据来源、许可证和使用边界见[数据卡](docs/data/data-card-v1.md)及 ml/data/source_manifest.json。接入真实企业数据前，需要获得授权并完成脱敏、访问控制和保留策略评审。
