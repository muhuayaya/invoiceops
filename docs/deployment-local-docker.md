# 本机 Docker 部署指南

本文说明如何在一台装有 Docker 的本机上启动 InvoiceOps。该方式与项目当前的虚拟机部署使用同一份 `docker-compose.yml`，PostgreSQL、Redis、MLflow、API、worker 和 Web 都由 Compose 管理。

## 1. 前置条件

- Docker Engine 或 Docker Desktop；
- Docker Compose v2（执行 `docker compose version` 能看到版本）；
- Git；
- 浏览器。

本机不需要另外安装 PostgreSQL 或 Redis。项目不会使用 Windows 上 phpstudy_pro 的 Redis；Compose 会启动自己的 `db` 和 `redis` 服务。

检查环境：

```powershell
docker --version
docker compose version
git --version
```

## 2. 获取代码并准备配置

```powershell
git clone <你的 GitHub 仓库地址>
Set-Location <项目目录>
Copy-Item .env.example .env
```

本机直接访问时，`.env` 中以下配置保持为本地地址即可：

```ini
VITE_API_BASE_URL=http://localhost:8000
INVOICEOPS_CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
DATABASE_URL=postgresql+psycopg://invoiceops:invoiceops@db:5432/invoiceops
REDIS_URL=redis://redis:6379/0
```

其中 `db` 和 `redis` 是 Compose 网络内的服务名，不能改成 `localhost`。 `VITE_API_BASE_URL` 是构建前端时写入静态文件的地址，修改后必须重新构建 `web` 服务。

## 3. 启动完整环境

```powershell
docker compose config
docker compose up --build -d
docker compose ps
```

首次构建会下载基础镜像和 Python/Node 依赖，耗时可能较长。启动成功后访问：

- Web 工作台：<http://localhost:3000>
- API 文档：<http://localhost:8000/docs>
- API 健康检查：<http://localhost:8000/healthz>
- API 就绪检查：<http://localhost:8000/readyz>

Windows PowerShell 可执行：

```powershell
curl.exe --fail http://localhost:8000/healthz
curl.exe --fail http://localhost:8000/readyz
curl.exe --fail http://localhost:3000/
```

默认演示账号：`admin/admin`、`reviewer/reviewer`、`observer/observer`。这些账号只适合本地演示，不能直接用于生产环境。

## 4. 服务与数据卷

| 服务 | 作用 | 默认端口 |
| --- | --- | --- |
| `db` | PostgreSQL 业务数据 | 仅 Compose 内部 |
| `redis` | Celery 队列和结果后端 | 仅 Compose 内部 |
| `mlflow` | 实验跟踪服务 | 仅 Compose 内部 |
| `migrate` | 一次性执行 Alembic 数据库迁移 | 无外部端口 |
| `api` | FastAPI 后端 | `8000` |
| `worker` | 批次任务消费者 | 无外部端口 |
| `web` | Nginx 托管的 React 页面 | `3000` |

`migrate` 是一次性任务，成功退出后 API 和 worker 才会启动。默认数据卷为 `postgres-data`、`redis-data`、`mlflow-data` 和 `batch-data`。普通停止或 `docker compose down` 不会删除这些数据；`docker compose down -v` 会删除数据卷，只能在明确要重置环境时使用。

查看日志：

```powershell
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f web
```

## 5. 更新代码和停止服务

```powershell
# 修改代码后重建并启动
docker compose up --build -d

# 暂停/恢复，保留数据
docker compose stop
docker compose start

# 移除容器，保留数据卷
docker compose down
```

## 6. 在虚拟机中部署

如果 Docker 实际运行在 Linux 虚拟机中，先通过 SSH 登录，再在项目目录执行同样的 Compose 命令：

```bash
ssh <虚拟机用户名>@<虚拟机IP>
cd <虚拟机项目目录>
cp .env.example .env
```

将 `.env` 中的前端地址改为虚拟机可被访问的地址：

```ini
VITE_API_BASE_URL=http://<虚拟机IP>:8000
INVOICEOPS_CORS_ORIGINS=http://<虚拟机IP>:3000
```

然后执行：

```bash
docker compose up --build -d
docker compose ps
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
```

浏览器使用 `http://<虚拟机IP>:3000` 访问。虚拟机防火墙只需要按访问场景放通 `3000` 和 `8000`；不要把 PostgreSQL、Redis 或 MLflow 端口暴露到公网。另一台物理机若无法路由到虚拟机私网 IP，需要使用桥接网卡或配置受限的 NAT 端口转发。

## 7. 常见问题

### 页面提示 `Failed to fetch`

检查 API：

```powershell
curl.exe --fail http://localhost:8000/healthz
```

再确认 `.env` 的 `VITE_API_BASE_URL` 和 `INVOICEOPS_CORS_ORIGINS` 与浏览器实际访问地址一致，并重新执行：

```powershell
docker compose up --build -d api web
```

### `readyz` 返回 503

查看依赖服务是否健康：

```powershell
docker compose ps
docker compose logs --tail=200 db redis mlflow api
```

### 批次没有处理

确认 `worker` 正在运行，CSV 使用 UTF-8 编码，并包含：

```csv
request_id,source,text,taxonomy_version
demo-001,email,"Payment is pending.",invoiceops-v1
```

### 当前到底使用哪个模型

线上 Compose 默认使用 `keyword-baseline-0.1` 确定性规则分类器。XLM-R v1/v2/v3 只完成了离线训练与评估，当前镜像没有打包模型权重。详见 [模型卡](model/model-card-v1.md)。
