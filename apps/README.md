# apps

应用入口目录，包含系统的三个运行服务。业务逻辑放在 `src/invoiceops/`，这里只负责对外接口、页面和后台任务。

| 目录 | 说明 | 运行方式 |
|---|---|---|
| [`api/`](api/) | FastAPI 接口服务 | `uvicorn apps.api.main:app` |
| [`web/`](web/) | Streamlit 工作台页面 | `streamlit run apps/web/app.py` |
| [`worker/`](worker/) | Celery 批量任务 worker | `celery -A apps.worker.tasks:celery_app worker --beat` |

Docker 部署时三者使用同一个镜像，由 `compose.yaml` 分别指定启动命令。
