# infra/migrations/versions

数据库迁移脚本，按编号顺序执行。

| 文件 | 说明 |
|---|---|
| `0001_initial.py` | 初始表结构：用户、会话、工单、分类结果、复核、批次、审计及各类历史表 |
| `0002_batch_item_history.py` | 批次删除后保留批次行的历史快照 |
| `0003_ticket_external_id.py` | 为单条分类增加可选的外部编号 |
| `0004_allow_self_review.py` | 允许提交人复核自己提交的工单 |

新增迁移时沿用编号前缀，并在 `down_revision` 中指向上一个版本。
