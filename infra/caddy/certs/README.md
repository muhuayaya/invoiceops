# infra/caddy/certs

`refresh_frontend_address.ps1` 在这里生成 `site.pem`（证书 + 私钥）和 `site.meta.txt`：证书由 Caddy 本地根证书签发，包含 `localhost`、`127.0.0.1` 和当前已连接网卡的 IP。本目录以只读方式挂载到 proxy 容器的 `/etc/caddy/certs`，Caddy 优先使用其中的证书；目录为空时退回 Caddy 自动签发。

生成的文件含私钥，不纳入版本库，可随时删除后重新运行脚本生成。
