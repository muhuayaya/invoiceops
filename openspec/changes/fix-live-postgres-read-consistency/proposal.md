## Why

批次 worker 在独立进程中将工单、预测和审计记录写入 PostgreSQL，但 API 进程中的 `PostgresRepository` 只在启动时加载一次内存快照。批次完成后，人工复核、数据管理、审计和运行指标仍然读取旧快照并显示为空，用户只能通过重启 API 临时看到数据。

## What Changes

- 为 PostgreSQL 仓储提供显式的读取刷新能力，用已提交的数据库数据更新 API 进程内的读取状态。
- API 的人工复核、复核历史、数据管理、审计和运行指标读取前刷新 PostgreSQL 仓储，使 worker 新写入的数据无需重启 API 即可展示。
- 增加跨进程写入后的集成回归测试，覆盖数据管理列表、复核队列和指标读取。
- 保持批次接口、数据库表结构、权限模型和前端 API 契约不变。

## Capabilities

### New Capabilities

- `live-postgres-read-consistency`: 定义独立 worker 写入 PostgreSQL 后，API 读接口必须在不重启服务的情况下反映已提交业务数据。

### Modified Capabilities

无。当前项目没有已发布的主规格目录；本变更新增该运行时一致性能力。

## Impact

- 影响 [`src/invoiceops/adapters/postgres.py`](../../../src/invoiceops/adapters/postgres.py) 的仓储刷新逻辑。
- 影响 [`apps/api/main.py`](../../../apps/api/main.py) 的复核、复核历史、管理员列表、审计和指标读接口。
- 增加 PostgreSQL/SQLite 方言兼容的集成测试，不新增第三方依赖、不修改数据库 schema。
- VM 需要重新构建并重启 API/worker 镜像；现有 PostgreSQL 数据卷保留不变。
