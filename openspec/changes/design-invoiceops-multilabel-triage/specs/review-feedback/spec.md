## Purpose

为低置信度、高风险和抽样质检工单提供人工复核闭环，使业务人员能够确认或纠正标签，同时保留可审计且可用于后续训练的数据来源。

## ADDED Requirements

### Requirement: Review queue
系统 SHALL 向有权限的复核人员展示待复核工单、模型建议、原因码、路由建议和必要的脱敏上下文，并支持按风险、时间和标签筛选。

#### Scenario: High-risk item ordering
- **WHEN** 队列同时存在普通低置信度工单和高风险工单
- **THEN** 系统默认将高风险工单置于更高优先级并明确标识风险原因

### Requirement: Accept or correct labels
复核人员 SHALL 能够接受模型标签、增加或删除允许的标签、选择主责任队列并提交复核备注。

#### Scenario: Correct model prediction
- **WHEN** 复核人员删除错误标签并添加正确标签后提交
- **THEN** 系统保存最终标签集合、复核人、时间、修改差异和备注

### Requirement: Immutable feedback history
系统 MUST 保留每次预测和复核动作的不可覆盖历史；后续修订 SHALL 追加新版本而不是覆盖旧记录。

#### Scenario: Second reviewer updates decision
- **WHEN** 第二位复核人员修订已审核工单
- **THEN** 系统保留前一版本并追加新的修订记录及关联关系

### Requirement: Training candidate governance
只有已完成复核且通过数据质量检查的记录 SHALL 被标记为训练候选；训练候选仍须记录其来源、脱敏状态和标签体系版本。

#### Scenario: Unreviewed prediction
- **WHEN** 工单只有模型预测而没有人工确认
- **THEN** 系统不得将其作为人工真值加入训练候选集

### Requirement: Review service levels
系统 SHALL 展示待复核记录的等待时长，并对超过配置时限的高风险记录产生可观察告警。

#### Scenario: Review SLA breach
- **WHEN** 高风险工单在配置时限内未被处理
- **THEN** 系统将其标记为逾期并发出告警事件
