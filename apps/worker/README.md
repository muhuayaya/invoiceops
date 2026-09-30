# apps/worker

Celery 后台 worker，负责 CSV 批量分类。

| 文件 | 说明 |
|---|---|
| `tasks.py` | `celery_app` 应用实例；`process_batch` 逐行分类一个批次；`resume_batches` 定时任务，每 60 秒扫描并继续处理未完成的批次 |

- 每一行分类结果单独提交，按“批次 ID + 行号”幂等处理，重启后不会重复生成工单。
- 模型不可用时批次暂停，模型恢复后自动继续。
- 使用 `solo` 池、并发 1，以便配合受监督的模型子进程。

## 启动

```powershell
uv run celery -A apps.worker.tasks:celery_app worker --beat --pool=solo --concurrency=1 --loglevel=INFO
```
