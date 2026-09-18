# ADR 0001：冻结 PoC 共享契约

**状态：接受**
**日期：2026-09-15**
**范围：阶段 1（M0）及其后续阶段的兼容性门禁**

## 决策

### 标签、路由与阈值

- 标签体系固定为 `invoiceops-v1`，标签回答“涉及什么问题”，允许一条工单命中多个标签。
- 路由规则独立于标签定义维护，回答“由谁处理”；路由变更不得改变历史预测的标签语义。
- 阈值配置使用独立的 `thresholds-v1` 版本；`SUPPLIER_MASTER_CHANGE` 属于高风险标签，始终进入人工复核。
- 共享资产：`configs/taxonomy/invoiceops-v1.json`、`configs/taxonomy/invoiceops-v1.schema.json`、独立的 `configs/taxonomy/invoiceops-v1.routes.json` 及其 Schema，以及 `configs/thresholds/thresholds-v1.json` 及其 Schema。

### API v1

- 公共路径前缀固定为 `/api/v1`。
- 单条分类请求必须携带 `Idempotency-Key`；相同键与相同请求体返回首次结果，相同键与不同请求体返回冲突错误。
- API 统一返回结构化错误，至少包含 `code`、`message`、`trace_id`，不得在错误或日志中写入工单原文或秘密。
- 当前契约覆盖单条分类、批次创建/查询、复核队列/决策、当前标签、请求审计和健康检查端点；批次输入以 UTF-8 CSV 模板为准。
- 共享资产：`docs/api/openapi-v1.yaml`、`docs/api/batch-template.csv`。

### 内部事件格式

所有接入、预测、路由、复核、模型提升和回滚事件使用同一 envelope，并只追加写入：

```json
{
  "event_id": "uuid",
  "event_type": "prediction.created",
  "event_version": "1",
  "occurred_at": "2026-09-15T00:00:00Z",
  "request_id": "request-id",
  "trace_id": "trace-id",
  "actor": {"type": "service", "id": "classifier"},
  "subject": {"type": "ticket", "id": "request-id"},
  "payload": {"taxonomy_version": "invoiceops-v1", "result_code": "classified"}
}
```

`payload` 只允许版本、状态、结果码、标识和必要摘要，不允许原始正文、凭据或未脱敏敏感字段。业务事实与 outbox 事件必须在同一事务中提交；历史事件不得 update/delete。

### PoC 验收口径

- 冻结测试集：Macro-F1 ≥ 0.80、Micro-F1 ≥ 0.85。
- `SUPPLIER_MASTER_CHANGE` recall ≥ 0.95；中英文 Macro-F1 差值 ≤ 0.08。
- 不调用外部 LLM 的单条预测 P95 ≤ 800 ms（至少 500 次，记录硬件与环境）。
- 高风险或低置信度结果 100% 进入人工复核；核心 API 验收压测错误率 < 1%。
- 模拟数据指标不得表述为真实企业收益；真实收益留待试点验证。

## 文件所有权

| 角色 | 负责人范围 | 共享契约责任 |
|---|---|---|
| A：数据与 ML | `ml/`、`configs/taxonomy/`、模型运行时 | 标签、阈值、模型 schema 与数据/模型指标 |
| B：后端与平台 | `apps/api`、`apps/worker`、`infra/` | OpenAPI、CSV、事件兼容性与部署 |
| C：前端与质量 | `apps/web`、`tests/e2e`、验收脚本 | 前端消费契约、E2E 场景与验收证据 |

## 变更规则与边界

- 破坏性标签、API 或事件变更必须升级版本；兼容性变更也必须更新契约测试和 ADR 引用。
- 共享契约变更须由对应负责人提交，并由另外两名角色确认后才可合并。
- 系统只提供分类建议、路由建议和人工复核，不写回 ERP，不触发付款，也不自动解除发票挂起。
- 高风险和低置信度结果不得因模型分数较高而绕过人工复核。

## 评审结论

阶段 1 的目录、依赖、标签契约、OpenAPI/CSV 契约和验证证据以本 ADR 及其引用文件为准；未满足上述门禁前不得进入阶段 2 的实现。
