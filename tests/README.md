# tests

pytest 自动化测试。

```powershell
uv run pytest tests -q
```

| 文件 | 覆盖内容 |
|---|---|
| `test_api.py` | 分类接口、权限、幂等、批次权限、模型就绪状态 |
| `test_api_history.py` | 自我复核、管理员增删改、批量删除、历史保留 |
| `test_application.py` | 多标签与低置信度判定、备用模型切换、CSV 解析 |
| `test_auth_db.py` | 注册登录、会话、管理员管理、密码规则、数据库迁移 |
| `test_authorization.py` | 权限规则 |
| `test_data_contract.py` | 数据契约校验 |
| `test_ml_runtime.py` | 模型文件完整性校验与备用模型规则 |
| `test_model_supervisor.py` | 模型子进程启动、超时和恢复 |
| `test_worker.py`、`test_worker_recovery.py` | 批量任务重启幂等、模型不可用时暂停与恢复 |
| `test_web.py` | 工作台页面、会话 Cookie、中文展示、前端密码检查 |
| `test_observability.py` | 指标与日志不含工单正文、限流 |
| `test_training.py`、`test_evaluate.py` | 训练指标与质量门槛评估 |
| `test_capacity_benchmark.py`、`test_fullstack_capacity_benchmark.py` | 压测工具与容量门槛评估 |

测试使用内存或临时 SQLite 数据库和测试替身模型，不需要启动 Docker；在没有 Redis 的环境下可设置 `INVOICEOPS_REDIS_URL=memory://`。
