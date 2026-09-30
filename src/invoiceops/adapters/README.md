# src/invoiceops/adapters

PostgreSQL 持久化层（SQLAlchemy 2）。

| 文件 | 说明 |
|---|---|
| `db.py` | 表结构定义、数据库连接和需要原子执行的业务写操作 |

## 主要表

| 表 | 说明 |
|---|---|
| `users`、`sessions` | 用户与登录会话（只保存令牌摘要） |
| `tickets`、`ticket_versions` | 工单及其修改版本 |
| `predictions` | 当前分类结果 |
| `review_decisions` | 复核决定 |
| `batch_jobs`、`batch_items` | 批次与批次行 |
| `prediction_history`、`batch_item_history` | 独立历史，删除业务数据后仍保留 |
| `audit_events` | 审计日志 |
| `model_releases` | 模型发布记录 |

主要操作：`create_ticket`、`revise_ticket`、`add_prediction`、`submit_review`（带版本号并发控制）、`delete_ticket` / `delete_prediction` / `delete_batch`、`list_review_queue`、`list_submission_history` 等。冲突抛出 `StateConflict`（HTTP 409），无权限抛出 `PermissionDenied`（HTTP 403）。
