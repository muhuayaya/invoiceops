# src

Python 源码目录。

| 路径 | 说明 |
|---|---|
| [`invoiceops/`](invoiceops/) | 核心业务包，被接口、工作台、worker 和训练脚本共同使用 |

`pyproject.toml` 已将 `src` 加入测试路径；Docker 镜像中通过 `PYTHONPATH=/app:/app/src` 引用。
