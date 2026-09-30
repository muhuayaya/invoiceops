# src/invoiceops/observability

运行监控。

| 文件 | 说明 |
|---|---|
| `monitoring.py` | `Metrics` 按模型版本、来源、语言和结果统计请求数与耗时，供 `/metrics` 输出；`RequestLimiter` 为登录和注册提供内存限流 |

统计和日志中不记录工单正文或账号密码。
