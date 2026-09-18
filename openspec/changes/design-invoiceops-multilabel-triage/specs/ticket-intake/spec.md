## Purpose

为中英文发票异常与付款咨询工单提供一致、可校验、可追踪的单条和批量接入契约，并确保进入分类链路的数据满足最小化与脱敏要求。

## ADDED Requirements

### Requirement: Single ticket intake
系统 SHALL 接受包含唯一请求标识、来源、正文和标签体系版本的单条分类请求；正文 SHALL 支持 UTF-8 编码的中文、英文或中英混合文本。

#### Scenario: Valid single request
- **WHEN** 调用方提交包含 `request_id`、`source`、`text` 和 `taxonomy_version` 的有效请求
- **THEN** 系统返回已受理结果，并为该请求分配可追踪的 `trace_id`

#### Scenario: Invalid request
- **WHEN** 请求缺少必填字段、正文为空、超出配置的长度限制或标签体系版本不存在
- **THEN** 系统返回结构化校验错误且不创建分类任务

### Requirement: Idempotent intake
系统 MUST 使用调用方提供的幂等键防止重复创建同一业务请求。

#### Scenario: Repeated idempotency key
- **WHEN** 调用方以相同幂等键和相同请求体重复提交
- **THEN** 系统返回首次请求的结果或任务标识，不创建第二条业务记录

#### Scenario: Conflicting idempotency key
- **WHEN** 调用方以相同幂等键提交不同请求体
- **THEN** 系统拒绝请求并返回幂等冲突错误

### Requirement: Batch CSV intake
系统 SHALL 接受符合模板的 UTF-8 CSV 文件并异步处理其中的工单，同时逐行报告成功或失败原因。

#### Scenario: Mixed-validity batch
- **WHEN** CSV 同时包含有效行和无效行
- **THEN** 系统处理有效行，并在批次结果中为每个无效行返回行号和错误原因

### Requirement: Data minimization and sanitization state
系统 MUST 只接受白名单元数据，并 MUST 在任何外部模型调用前确认文本已完成脱敏；未通过脱敏检查的文本不得出域。

#### Scenario: Unknown metadata field
- **WHEN** 请求包含未在白名单中的元数据字段
- **THEN** 系统忽略或拒绝该字段并记录结构化校验事件，不将其送入模型

#### Scenario: Unsanitized low-confidence ticket
- **WHEN** 工单需要 LLM 辅助但脱敏状态不是 `sanitized`
- **THEN** 系统跳过外部 LLM 并将工单送入人工复核
