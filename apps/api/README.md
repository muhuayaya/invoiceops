# apps/api

FastAPI 接口服务，所有鉴权和权限判断都在这里完成。

| 文件 | 说明 |
|---|---|
| `main.py` | `create_app()` 创建应用：加载主、备模型，注册全部接口；模块级 `app` 供 uvicorn 启动 |

## 接口分组

| 分组 | 路径 |
|---|---|
| 健康检查 | `/healthz`、`/readyz` |
| 认证 | `/v1/auth/register`、`/v1/auth/login`、`/v1/auth/logout`、`/v1/auth/me` |
| 分类 | `/v1/classifications` |
| 批量 | `/v1/batches`、`/v1/batches/{id}`、`/v1/batches/{id}/download`、`/v1/batches/{id}/errors` |
| 复核 | `/v1/reviews/queue`、`/v1/reviews/history`、`/v1/reviews/{ticket_id}` |
| 管理员 | `/v1/admin/users`、`/v1/admin/tickets`、`/v1/admin/predictions`、`/v1/admin/batches`、`/v1/admin/history` |
| 指标 | `/metrics`（仅管理员） |

请求体使用 Pydantic 模型校验，例如 `Credentials`、`ClassificationInput`、`ReviewInput`、`TicketInput`、`BulkDelete`。注册和登录按邮箱与 IP 限流。

## 启动

```powershell
uv run uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
```

需要设置 `INVOICEOPS_DATABASE_URL`、`INVOICEOPS_PRIMARY_MODEL`、`INVOICEOPS_FALLBACK_MODEL` 等环境变量，完整列表见根目录 `README.md`。
