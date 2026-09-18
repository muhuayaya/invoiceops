# M0–M4 验收证据（v1）

| 验收项 | 证据 | 结论 |
|---|---|---|
| 单条中英混合多标签 | `tests/integration/test_api_flow.py`、`docs/delivery/demo-evidence-v2.json` | 通过 |
| 低置信度/高风险转人工 | `tests/unit/test_model_runtime.py`、集成测试 | 通过 |
| 幂等冲突 | `tests/integration/test_api_flow.py` | 通过 |
| 批次部分失败 | `tests/integration/test_batch_flow.py`、`docs/delivery/demo-evidence-v2.json` | 通过（本地 background/Celery fallback） |
| 认证与角色矩阵 | `tests/unit/test_auth.py`、集成测试 | 通过 |
| 审计查询与指标 | API 集成测试、`docs/delivery/demo-evidence-v2.json` | 通过 |
| 管理员数据管理 | `tests/integration/test_admin_data_management.py`、VM admin smoke | 手工录入/列表筛选/单条与批量删除/批量覆盖及幂等、权限校验通过 |
| 复核 SLA 与修订历史 | `tests/integration/test_api_flow.py`、`GET /api/v1/reviews/{ticket_id}/history` | 等待时长、逾期审计告警、第二次修订历史通过 |
| 前端工作台构建 | `npm run check && npm run build` | 通过 |
| XLM-R 训练、冻结评估与性能约束 | `docs/model/training-record-v3.json`、`docs/model/xlmr-evaluation-v3.json`、`docs/performance/performance-report-v1.json` | v3 离线评估通过；本机 5 并发压测 observed QPS=8.42、P95=635.16ms、错误率=0，5,000 字符和 10,000 行批次门禁通过；尚未打包到 serving 镜像 |
| Redis/Celery 重启恢复 | `tests/integration/test_worker_recovery.py`、`tests/integration/test_live_celery_worker_recovery.py`、`docs/delivery/live-recovery-evidence-v1.json`、`docs/delivery/live-recovery-vm/` | phpstudy Redis + SQLite 及 VM Compose PostgreSQL + Redis 的真实 worker 重启均通过，唯一 run ID 核对无重复记录 |
| 干净环境冷启动 | `docs/delivery/cold-start-evidence-v1.json`、`docs/delivery/compose-smoke-evidence-v1.json`、`docs/delivery/final-signoff-v1.md`、`docs/delivery/user-attestation-v1.json` | VM 内完整 Compose 冷启动、6 个长期服务健康检查和数据库迁移任务通过；独立成员门禁按用户授权确认完成 |

最终状态：M0–M3 的 PoC 骨架、真实 XLM-R 训练/冻结评估、持久化仓储、异步恢复代码、复核闭环、管理员数据管理和 CI 配置已完成；当前本地门禁为 45 passed、1 skipped，v3 离线模型门禁已解除。VM 内完整 Compose、PostgreSQL/Redis worker 恢复和业务演示已通过；8.5、8.6 按用户授权确认完成。该授权不等同于生产就绪认证。
