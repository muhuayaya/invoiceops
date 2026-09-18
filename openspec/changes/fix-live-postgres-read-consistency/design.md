## Context

当前 API 和 Celery worker 是独立进程。API 启动时创建 PostgreSQL 仓储并将数据库内容加载到内存；worker 使用自己的仓储实例处理批次并提交 PostgreSQL。API 的多个读接口随后继续读取启动时的集合。详见 proposal.md 的 Why，以及 `live-postgres-read-consistency` 规格。

## Goals / Non-Goals

**Goals:**

- 在 API 读接口执行前同步已提交的 PostgreSQL 数据；
- 覆盖工单列表、人工复核、复核历史、审计和运行指标等依赖仓储快照的读路径；
- 不改变现有 API 响应、角色权限、脱敏规则、批次协议和数据表结构；
- 用跨仓储实例的集成测试复现 worker 写入、API 读取场景。

**Non-Goals:**

- 不把批次状态表迁移到 PostgreSQL；批次检查点仍使用共享 `/data` SQLite 文件；
- 不引入消息推送、WebSocket 或新的缓存系统；
- 不重写整个仓储为 ORM 查询型实现；
- 不修改 SQLite、内存仓储的既有行为。

## Decisions

### 1. API 读请求显式刷新 PostgreSQL 仓储

在 PostgreSQL 仓储增加刷新操作，重新读取已提交的工单、预测、复核、审计、Outbox、LLM 建议和幂等记录。API 在相关读路由及依赖已有记录的复核/管理员写路由调用该操作。

选择显式刷新是因为当前应用服务大量使用仓储的领域对象集合（例如工单列表和指标计数），可以用较小改动修复所有相关读路径。相比只在某个列表方法中补查询，它不会遗漏指标、审计和复核历史；相比重写全部服务为数据库查询，改动面和兼容风险更小。

刷新仅对具有该能力的 PostgreSQL 仓储执行；SQLite 和内存仓储保持原有进程内语义。刷新不改变任何数据，只读取数据库并替换当前进程的只读快照。

### 2. 快照重建必须避免历史集合重复

刷新不能直接重复调用现有加载逻辑而保留原列表，否则审计和 Outbox 会重复。加载过程将先构造完整的新快照，再一次性替换仓储中的集合，保证同一次刷新产生一致的数据视图。

### 3. 回归测试使用两个共享数据库仓储实例

测试用 SQLite URL 模拟 PostgreSQL 仓储的持久化行为：API 仓储先启动并保持空快照，另一个“worker”仓储写入工单，随后通过 API 的读接口验证新数据可见。这样不依赖 Docker 或外部 PostgreSQL，同时覆盖跨进程写入的关键语义。

## Risks / Trade-offs

- [Risk] 每次相关读请求都会重新读取 PostgreSQL，增加数据库查询开销 → [Mitigation] 只在 API 的相关路由刷新，不在 worker 的每行处理路径刷新；当前 PoC 数据量和请求量适合该方案。
- [Risk] 刷新期间数据库连接异常会使读请求失败 → [Mitigation] 保留现有数据库连接和 API 异常处理行为，不用不完整快照覆盖当前状态；通过就绪检查和日志暴露依赖问题。
- [Risk] 多个 API 请求同时刷新可能增加并发查询 → [Mitigation] 刷新构造完整快照后再替换，读接口仍获得完整对象集合；后续高吞吐场景可再引入 TTL 或数据库查询型仓储。

## Migration Plan

1. 更新 API 仓储和读路由代码；
2. 执行单元、集成和契约测试；
3. 将变更后的源码同步到 VM `/home/itheima/invoiceops`；
4. 在 VM 执行 `docker compose up --build -d api worker`；
5. 上传 CSV，确认批次完成后无需重启 API，人工复核、数据管理、审计和指标均可看到新数据；
6. 如需回滚，使用上一版本 API/worker 镜像重新部署，PostgreSQL 数据卷无需删除。
