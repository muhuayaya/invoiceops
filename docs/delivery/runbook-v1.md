# InvoiceOps 运行与演示手册（v1）

## 一键启动

在干净主机安装 Docker Compose 后，Compose 会把不含秘密的 `.env.example` 作为基础环境文件；如需覆盖 API 地址或其他值，再复制为 `.env`：

```powershell
Copy-Item .env.example .env
docker compose up --build
```

不需要覆盖配置时可直接执行 `docker compose up --build`，这是冷启动验收的一条启动命令。

API 位于 `http://localhost:8000`，工作台位于 `http://localhost:3000`。如果 Compose 在虚拟机内运行，请使用虚拟机 IP 访问。

## Windows 主机与虚拟机配置边界

推荐在 Docker 虚拟机内运行完整 Compose，让 PostgreSQL、Redis、API、worker 和 Web 使用 Compose 服务名互联，并在 API/worker 启动前通过 `migrate` 服务执行 Alembic 迁移；不要让容器跨网络依赖 Windows 上的 phpstudy Redis。虚拟机需要 Docker Engine、Compose v2，并能访问项目目录；若从 Windows 触发验收，需要提供虚拟机 IP 和 SSH 连接方式，同时放通 3000/8000 端口。

例如本次 VM `VM_IP` 的部署步骤如下（命令在 VM 项目目录执行）：

```bash
cp .env.example .env
sed -i 's#^VITE_API_BASE_URL=.*#VITE_API_BASE_URL=http://VM_IP:8000#' .env
sed -i 's#^INVOICEOPS_CORS_ORIGINS=.*#INVOICEOPS_CORS_ORIGINS=http://VM_IP:3000#' .env
docker compose up --build -d
curl --fail http://VM_IP:8000/healthz
curl --fail http://VM_IP:3000/
```

phpstudy_pro 的 Redis 只适合宿主机上的 live worker 验证：启动 `redis-server.exe` 后确认 `127.0.0.1:6379` 可连，再设置 `REDIS_URL=redis://127.0.0.1:6379/0`。该方式仍需要独立 PostgreSQL；MySQL 不能替代项目所需的 PostgreSQL。不要把 Redis 绑定到公网地址或在未配置认证时暴露到外网。

CI 的 `compose-smoke` job 会在干净 Ubuntu runner 上执行 `docker compose up --build -d`，并轮询 API 与 Web 健康状态后清理卷；同一流水线的 Redis service 会让首个 Celery worker 实际消费批次、在持久检查点后强制终止，再由唯一 hostname 的第二个 worker 恢复并核对业务记录无重复。两个 job 会分别上传 `invoiceops-compose-evidence` 和 `invoiceops-live-recovery-evidence`，其中包含 Compose 状态/日志、worker 日志与 live test 的 JUnit/pytest 日志；CI 或受控 VM 的外部运行结果需保存后，才能把对应 OpenSpec 门禁标记为完成。

Compose 中 `DATABASE_URL` 指向 PostgreSQL，API 和 worker 共享同一业务库；批次 payload/检查点使用共享 `/data` 卷。未配置 `DATABASE_URL` 的本地开发模式才使用 SQLite fallback。
Web 镜像在构建时将 `VITE_API_BASE_URL` 默认编译为 `http://localhost:8000`，因此 Compose 页面连接真实 API；API 通过 `INVOICEOPS_CORS_ORIGINS` 限定允许的 Web 来源。如端口、IP 或域名变化，需同时更新这两个变量后重新构建。

## 跨机器访问

当前 VM 使用 VMware NAT，`VM_IP` 适用于宿主机或已接入 VMnet8 的机器。另一台物理机若不在该虚拟网络中，不能直接路由到这个地址。可将 VM 网卡改为 Bridged，使用 VM 获得的局域网 IP；或在 VMnet8 NAT 中仅转发 TCP `3000` 和 `8000` 到 `VM_IP`，然后使用宿主机局域网 IP 访问。不要暴露 PostgreSQL `5432`、Redis `6379` 或 MLflow 端口。

如果通过新地址（例如 `http://192.168.34.175:3000`）访问，先在 `.env` 中同步设置：

```ini
VITE_API_BASE_URL=http://192.168.34.175:8000
INVOICEOPS_CORS_ORIGINS=http://192.168.34.175:3000
```

随后执行 `docker compose up --build -d api web`。在验收机上确认页面能打开、登录能提交，并用 `Test-NetConnection <访问地址> -Port 3000` 和 `Test-NetConnection <访问地址> -Port 8000` 检查两个端口。

## 核心演示

演示账号：`admin/admin`（管理员）、`reviewer/reviewer`（人工复核）、`observer/observer`（只读）。上线前必须替换为企业身份源和部署密钥。

```powershell
python scripts/run_demo.py
```

脚本把实际多标签、路由/版本、复核、审计、批次错误明细和指标摘要写入 `docs/delivery/demo-evidence-v2.json`。

手工 M0–M4 演示顺序：登录 reviewer，提交中英混合文本并确认多标签与人工复核；在复核队列接受或改标并查看等待时长/修订历史；按 `request_id` 查看审计时间线；上传含有效行和坏行的 CSV 并查看批次错误；最后打开运行看板确认指标。管理员另行演示数据管理、模型回滚和数据清理，observer 只验证只读权限。

管理员数据管理演示：登录 `admin/admin` 后打开“数据管理”。“手工录入”会先脱敏再分类；列表支持请求编号、风险和状态筛选，可勾选后批量删除，也可单条删除。批量覆盖只允许覆盖已存在的请求编号，CSV 必须包含 `request_id,source,text,taxonomy_version` 四列，整批校验通过后才会执行；删除和覆盖均要求幂等键并保留审计记录。

## 故障降级

- LLM 默认关闭；未脱敏、超预算、超时或熔断时保留主模型结果并进入人工复核。
- Redis/Celery 不可用时，开发模式退回 FastAPI background task；生产模式应修复队列依赖，不把 fallback 当作高可用保证。
- `/healthz` 只表示进程存活；`/readyz` 在模型未加载时返回 503。

## 模型回滚与清理

- 通过 `ModelRegistry.rollback()` 回滚到上一稳定版本，并检查审计事件。
- 管理员调用 `POST /api/v1/admin/retention/cleanup` 执行保留期清理；清理证明必须保留。

## 独立成员验收记录

另一名成员应在不依赖当前 VM 数据卷的机器上执行一键启动和 M0–M4 演示。记录以下信息后，才能将 OpenSpec 8.5、8.6 标记完成：

```text
执行者：
日期/时区：
操作系统、Docker、Compose、浏览器：
代码版本或提交哈希：
启动命令及结果：
healthz/readyz/Web 检查结果：
演示 request_id、batch_id、审计事件数：
截图/日志/evidence 文件路径：
执行者签字：
```
