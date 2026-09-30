# src/invoiceops/ml_runtime

在线推理的模型加载与运行。

| 文件 | 说明 |
|---|---|
| `runtime.py` | `load_model` 加载模型目录前校验文件哈希、标签顺序和阈值；不完整或不一致的模型抛出 `ModelLoadError` |
| `supervisor.py` | `ModelSupervisor` 在独立子进程中运行模型，启动和单次推理都有超时；超时会结束子进程并自动重启 |

超时时间由环境变量 `INVOICEOPS_MODEL_STARTUP_TIMEOUT_SECONDS`（默认 180 秒）和 `INVOICEOPS_MODEL_INFERENCE_TIMEOUT_SECONDS`（默认 10 秒）控制。`INVOICEOPS_DEMO_MODE=false` 时，模型目录还需要 `approval.json` 才能上线。
