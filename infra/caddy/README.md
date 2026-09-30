# infra/caddy

Caddy 反向代理配置，负责 HTTPS 入口。

| 文件 | 说明 |
|---|---|
| `Caddyfile` | 正式部署配置：使用本地 CA 签发证书；`/healthz`、`/readyz` 转发到 API，其余请求转发到 Streamlit 工作台 |
| `Caddyfile.capacity` | 压测环境配置：额外转发 `/v1/*`、`/metrics` 和压测标识接口 |
| `certs/` | `refresh_frontend_address.ps1` 生成的多地址证书 `site.pem`（含私钥，不纳入版本库），挂载到容器 `/etc/caddy/certs` |
| `root.crt` | 从容器导出的本地 CA 公开根证书（不纳入版本库），供访问设备安装信任 |

证书主机名由环境变量 `INVOICEOPS_HOST` 提供，同时签发 `localhost` 证书。用 IP 访问时浏览器不发送 SNI，Caddy 返回 `INVOICEOPS_HOST` 的证书（`default_sni`）。

站点地址末尾的 `:443` 接收任意主机名/IP 的请求，再由 `INVOICEOPS_ALLOWED_HOSTS`（空格分隔）决定放行哪些地址，其余返回 421。未设置时默认放行 `localhost` 和任意 IPv4。根目录的 `refresh_frontend_address.ps1` 会把它设为 `localhost 127.0.0.1` 加上当前已连接网卡的 IP，并让 `INVOICEOPS_HOST` 跟随当前上网网卡。

导出根证书：

```powershell
docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt .\infra\caddy\root.crt
```
