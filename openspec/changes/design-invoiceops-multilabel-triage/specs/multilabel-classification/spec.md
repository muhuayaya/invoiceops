## Purpose

为中英文及中英混合工单提供可解释、可拒识的多标签分类与队列建议，并用人工复核和受控 LLM 兜底约束高风险或不确定结果。

## ADDED Requirements

### Requirement: Bilingual multi-label prediction
系统 SHALL 对每条工单独立计算各业务标签的概率，并返回所有达到对应标签阈值的标签、分数和标签体系版本；一条工单可以返回多个标签。

#### Scenario: Multiple applicable issues
- **WHEN** 工单同时描述采购订单价格差异和税额问题，且两个标签均达到各自阈值
- **THEN** 系统同时返回 `PRICE_VARIANCE` 与 `TAX_CURRENCY_AMOUNT` 及各自分数

#### Scenario: Mixed Chinese and English
- **WHEN** 正文同时包含中文和英文业务描述
- **THEN** 系统在同一标签体系下完成预测，并返回检测到的语言类型

### Requirement: Versioned decision response
每次分类结果 MUST 包含请求标识、决策状态、预测标签、模型版本、阈值版本、标签体系版本、推理耗时和 `trace_id`。

#### Scenario: Successful classified decision
- **WHEN** 主模型成功完成高置信度预测且没有触发强制复核规则
- **THEN** 系统返回 `classified` 状态和完整版本信息

### Requirement: Confidence rejection
系统 MUST 在没有标签达到自动接受阈值、标签间冲突、预测分布异常或整体置信度不足时返回 `needs_review`，而不是强制给出自动分类结论。

#### Scenario: No label reaches threshold
- **WHEN** 所有标签得分均低于对应自动接受阈值
- **THEN** 系统返回 `needs_review` 并给出 `LOW_CONFIDENCE` 原因码

### Requirement: High-risk override
供应商收款账户或主数据变更等高风险标签 MUST 始终进入人工复核，无论模型置信度多高。

#### Scenario: High-confidence bank detail change
- **WHEN** `SUPPLIER_MASTER_CHANGE` 得分超过自动接受阈值
- **THEN** 系统仍返回 `needs_review` 并给出 `HIGH_RISK_LABEL` 原因码

### Requirement: Controlled LLM fallback
系统 SHALL 仅在低置信度策略允许、文本已脱敏、外部服务可用且预算未超限时调用配置的 LLM；LLM 只能从当前标签体系选择标签，并 MUST 返回可校验的结构化结果。

#### Scenario: Eligible LLM assistance
- **WHEN** 主模型置信度不足、文本已脱敏且 LLM 策略条件全部满足
- **THEN** 系统保存 LLM 建议及提供商/模型标识，并将最终结果送入人工复核

#### Scenario: Invalid LLM response
- **WHEN** LLM 返回未知标签、格式无效、超时或调用失败
- **THEN** 系统忽略该建议、记录失败原因并继续人工复核流程

### Requirement: Route recommendation
系统 SHALL 根据标签集合、风险规则和版本化路由表生成一个主队列建议及可选协同队列，不把路由建议视为付款或 ERP 操作授权。

#### Scenario: Multi-team recommendation
- **WHEN** 预测同时包含税务问题和价格差异
- **THEN** 系统根据路由表返回一个主责任队列和一个协同队列，并包含路由规则版本

### Requirement: PoC performance envelope
在约定的 PoC 测试硬件和不调用外部 LLM 的情况下，单条主模型推理 P95 SHALL 不高于 800 毫秒；外部 LLM 调用 SHALL 使用独立超时且不得阻塞批量任务的其他记录。

#### Scenario: Performance acceptance run
- **WHEN** 使用冻结性能数据集在约定硬件上执行至少 500 次单条预测
- **THEN** 报告 SHALL 显示主模型 P95 不高于 800 毫秒并列出测试环境
