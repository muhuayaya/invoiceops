## Why

企业财务共享中心每天会收到中英文供应商邮件和内部工单，其中同一条文本可能同时涉及价格差异、收货数量、税务、付款状态或供应商主数据等多个问题。人工阅读、打标签和转派容易造成重复流转、响应延迟与审计信息缺失；Oracle Payables 的发票校验流程也明确包含税额、订购/收货/开票数量或金额差异、汇率及异常挂起等业务情形，[Microsoft 的企业级统一路由](https://learn.microsoft.com/en-us/dynamics365/customer-service/administer/overview-unified-routing)则将“分类”和“分派”定义为两个独立阶段。

本变更拟建立一个可运行的企业级 PoC：先对脱敏的中英文工单执行多标签分类，再依据置信度、风险等级和路由规则给出队列建议；不直接修改 ERP、释放发票挂起或触发付款。

## What Changes

- 建立中英文供应商发票异常与付款咨询工单的统一接入格式，支持单条 API 与 CSV 批量导入。
- 建立版本化的多标签体系，首版覆盖价格差异、数量/收货差异、税务/币种/金额异常、重复发票、缺少采购凭证、付款状态、供应商主数据变更和其他人工复核。
- 建立主分类模型与基线模型对照：轻量模型用于成本/时延基线，多语言 Transformer 用于主要推理，外部云端 LLM 只处理通过脱敏和策略检查的低置信度样本。
- 建立置信度校准、拒识和风险覆盖规则；高风险或低置信度结果必须进入人工复核。
- 建立复核工作台，记录人工接受、改标和备注，并将已审核反馈沉淀为后续训练数据候选。
- 建立预测审计、模型/标签/数据版本追踪、运行监控和最小角色权限。
- 建立适合 3 人短周期协作的模块化单体、异步任务 worker、CI 门禁与 Docker Compose 运行方式。
- 明确 PoC 不包含 OCR/PDF 解析、生产 ERP 写回、自动付款、自动解除挂起、自动修改供应商银行账户、生产级高可用和跨地域容灾。

## Capabilities

### New Capabilities

- `ticket-intake`: 定义单条和批量工单接入、输入校验、脱敏状态及幂等行为。
- `multilabel-classification`: 定义中英文多标签预测、置信度、拒识、风险覆盖和受控 LLM 兜底行为。
- `review-feedback`: 定义人工复核队列、接受/改标、反馈留痕和训练候选数据闭环。
- `model-lifecycle`: 定义标签、数据集和模型的版本管理、离线评估、准入与回滚。
- `security-audit-observability`: 定义角色权限、敏感数据保护、预测审计、健康检查、指标和日志。

### Modified Capabilities

无。当前项目为绿地项目，尚不存在基线能力规格。

## Impact

- 新建 Python 后端、训练流水线、Web 复核台、PostgreSQL 数据模型、对象存储抽象、异步 worker 和项目级 CI/CD 配置。
- 对外提供版本化 REST API；PoC 通过 Docker Compose 在单机或内部服务器运行。
- 引入 FastAPI、PostgreSQL、Redis、MLflow、PyTorch/Transformers 与 React/TypeScript；外部 LLM 通过可替换适配器接入。
- 数据来自公开数据与企业场景模拟数据，仅用于 PoC；所有业务效果目标均为验收门槛或待验证假设，不表述为已实现收益。
