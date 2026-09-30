# ml/gates

模型质量与系统容量的门槛配置（JSON），供评估和压测脚本读取。

| 文件 | 说明 | 使用方 |
|---|---|---|
| `engineering-quality-v1.json` | 质量门槛：macro-F1 ≥ 0.80、micro-F1 ≥ 0.85、`SUPPLIER_MASTER_CHANGE` 召回率 ≥ 0.95、中英 macro-F1 差距 ≤ 0.08；并登记被评估的模型版本 | `ml/evaluate.py` |
| `engineering-capacity-v1.json` | 完整容量门槛：六服务部署下的并发档位、吞吐、延迟、错误率、资源占用和持续运行要求 | `run_fullstack_capacity_benchmark.ps1`、测试 |
| `engineering-capacity-short-v1.json` | 短时容量检查：并发 1/2/3，适合小规模部署 | `run_fullstack_capacity_benchmark.ps1 -GatePath ...` |

门槛文件确定后不应根据测试或压测结果修改；需要调整时新建一个版本号的文件。
