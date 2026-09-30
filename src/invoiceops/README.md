# src/invoiceops

核心业务包。

| 子包 | 说明 |
|---|---|
| [`adapters/`](adapters/) | 数据库表结构与读写操作 |
| [`application/`](application/) | 分类与复核判定、文本脱敏、CSV 批量解析 |
| [`data/`](data/) | 训练数据契约与校验 |
| [`ml_runtime/`](ml_runtime/) | 模型加载校验与子进程推理监督 |
| [`observability/`](observability/) | 请求指标与限流 |
| [`security/`](security/) | 账号、密码、会话与权限规则 |

依赖方向：`apps/` 调用本包；本包内 `application` 不依赖具体的数据库或 Web 框架。
