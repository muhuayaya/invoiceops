# InvoiceOps 威胁与数据泄漏走查（v1）

## 已验证控制

- API 仅记录 `trace_id`、状态码、版本和组件，不记录工单正文、凭据或 JWT。
- 工单进入持久化/模型前执行规则脱敏；LLM 默认关闭，未脱敏输入不会送出。
- 复核与审计查询受 JWT 角色矩阵保护；保留清理会追加审计证明事件。
- Compose 中 API/worker 通过 `DATABASE_URL` 共享 PostgreSQL 业务仓储；本地 SQLite 仓储仅作为无数据库 URL 的可测试 fallback。
- 幂等键冲突返回 409，避免重试重复创建业务记录。
- `.env.example` 只含本地占位秘密，真实秘密必须由部署环境注入。

## 上线前必须补齐

| 风险 | 等级 | 处置 | 状态 |
|---|---|---|---|
| 本地种子账号仍为演示凭据 | P1 | 接入企业身份源并强制轮换 JWT secret | 未关闭 |
| 生产批次仍需真实 Redis/PostgreSQL 联合验收 | P1 | 当前已有 SQLite 检查点、PostgreSQL 仓储和 Celery task；生产环境仍需验证 Redis broker、数据库业务记录和 worker 重启 | 未关闭 |
| XLM-R v3 尚未进入 serving | P1 | v3 已通过冻结离线门禁；上线前需完成制品打包、serving 配置、shadow 验证和回滚演练 | 未关闭 |
| 镜像扫描与多成员冷启动未执行 | P2 | 在 CI/干净主机执行并保存证据 | 未关闭 |

结论：没有未关闭 P0 风险；项目仍是 PoC，以上 P1/P2 项不得被描述为生产就绪。模型来源校验与训练/评估证据见 `docs/model/model-source-manifest-v1.json`、`docs/model/training-record-v1.json`、`docs/model/training-record-v2.json`、`docs/model/training-record-v3.json` 和对应评估报告。
