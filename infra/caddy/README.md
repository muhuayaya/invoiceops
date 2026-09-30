# infra/caddy

Caddy 反向代理配置，负责 HTTPS 入口。

| 文件 | 说明 |
|---|---|
| `Caddyfile` | 正式部署配置：使用本地 CA 签发证书；`/healthz`、`/readyz` 转发到 API，其余请求转发到 Streamlit 工作台 |
| `Caddyfile.capacity` | 压测环境配置：额外转发 `/v1/*`、`/metrics` 和压测标识接口 |
| `root.crt` | 从容器导出的本地 CA 公开根证书（不纳入版本库），供访问设备安装信任 |

证书主机名由环境变量 `INVOICEOPS_HOST` 提供，同时签发 `localhost` 证书。站点地址末尾的 `:443` 接收任意主机名/IP 的请求，配合 `INVOICEOPS_BIND_ADDRESS=0.0.0.0`，本机切换有线/无线网络后用新 IP 仍能访问；用 IP 访问时浏览器不发送 SNI，Caddy 返回 `INVOICEOPS_HOST` 的证书（`default_sni`）。运行根目录的 `refresh_frontend_address.ps1` 可让 `INVOICEOPS_HOST` 跟随当前上网网卡。

导出根证书：

```powershell
docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt .\infra\caddy\root.crt
```
