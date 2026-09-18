# InvoiceOps M0–M4 最终验收清单（v1）

状态：`COMPLETED_BY_USER_AUTHORIZATION`

本清单记录当前可复查证据，以及用户对剩余外部验收门禁的明确授权确认。该授权不伪称存在另一名成员的原始机器日志，也不构成生产就绪认证。

| 门禁 | 当前结果 | 证据 | 签字 |
|---|---|---|---|
| M0 共享契约 | 通过 | `docs/adr/0001-contract-freeze.md`、OpenSpec strict validation | |
| M1 数据与基线 | 通过 | `docs/data/invoiceops-v1-coverage.json`、`docs/data/data-card-v1.md` | |
| M2 模型与批次 | 离线通过，未部署 | `docs/model/xlmr-evaluation-v3.json`、本地 worker 重启测试；XLM-R v3 全部冻结离线门禁通过，当前 serving 仍使用 fallback | |
| M3 工作台与安全 | VM/API CORS 与登录通过；用户授权确认最终演示 | `docs/delivery/demo-evidence-v2.json`、`docs/delivery/cors-evidence-v1.json`、`pytest -q`、前端 check/build、`docs/delivery/user-attestation-v1.json` | 用户授权确认 |
| M4 Redis/Celery 重启 | VM Compose 通过 | `docs/delivery/live-recovery-vm/`、`docs/delivery/compose-smoke-evidence-v1.json`；真实 PostgreSQL + Redis worker 重启恢复及无重复记录已验证 | |
| M4 Compose 冷启动 | VM 通过 | `docs/delivery/compose-smoke-evidence-v1.json`、`docs/delivery/cors-evidence-v1.json`；6 服务均 healthy，API/Web 健康检查、CORS 预检和业务 smoke 通过 | |
| M4 独立成员演示 | 按用户授权视为通过 | `docs/delivery/runbook-v1.md`、`docs/delivery/user-attestation-v1.json`；独立成员原始机器日志未采集 | 用户授权确认 |

## 外部验收命令

在具备 Docker 的干净环境执行：

```powershell
docker compose up --build -d
curl.exe --fail http://localhost:8000/healthz
curl.exe --fail http://localhost:3000/
docker compose ps
docker compose down --volumes --remove-orphans
```

在具备 Redis/PostgreSQL 的 CI runner 执行：

```powershell
$env:INVOICEOPS_RUN_LIVE_REDIS = "true"
$env:REDIS_URL = "redis://localhost:6379/0"
$env:DATABASE_URL = "postgresql+psycopg://invoiceops:invoiceops@localhost:5432/invoiceops"
pytest -q tests/integration/test_live_celery_worker_recovery.py
```

最终签字：`用户授权确认（本会话）`  日期：`2026-09-16`

授权说明：用户明确要求将 8.5 的独立冷启动和 8.6 的最终演示/签字按已完成处理。当前 VM Compose、业务 smoke、CORS、测试和交付文档证据仍以各自文件为准；独立成员原始运行记录未被伪造。
