## Purpose

为 PoC 提供与企业使用场景相称的最小权限、敏感数据保护、完整审计和可观测性，使分类结果能够安全演示、追责和排障。

## ADDED Requirements

### Requirement: Role-based access
系统 MUST 至少区分管理员、复核人员和只读观察者，并按最小权限限制标签配置、模型提升、工单复核和审计查询操作。

#### Scenario: Observer attempts correction
- **WHEN** 只读观察者尝试修改工单标签
- **THEN** 系统拒绝操作并记录授权失败事件

### Requirement: Secret and external provider protection
外部服务凭据 MUST 通过运行环境秘密注入，不得存储在源码、数据集、日志或前端；外部 LLM 请求 MUST 只包含完成分类所需的最少脱敏字段。

#### Scenario: LLM request preparation
- **WHEN** 系统准备调用外部 LLM
- **THEN** 请求负载不包含原始供应商名称、邮箱、银行信息、发票号码或未授权元数据

### Requirement: Prediction audit trail
每次接入、分类、LLM 辅助、路由、复核、模型提升和回滚 MUST 产生带操作者或服务主体、时间、对象标识、版本和结果的审计事件。

#### Scenario: Trace a corrected prediction
- **WHEN** 审计人员按 `request_id` 查询已改标工单
- **THEN** 系统返回从接入、模型预测到全部人工修订的有序事件链

### Requirement: Safe logging
应用日志 MUST 使用结构化字段并包含 `trace_id`、阶段、耗时、结果码和版本标识；默认不得记录完整原文、凭据或敏感元数据。

#### Scenario: Inference error log
- **WHEN** 推理发生异常
- **THEN** 日志包含可排障的错误类型和追踪信息但不包含工单原文

### Requirement: Health and metrics endpoints
系统 SHALL 提供存活、就绪和指标端点；就绪状态 MUST 反映数据库、主模型和必要依赖是否可服务。

#### Scenario: Model unavailable
- **WHEN** API 进程存活但当前模型未成功加载
- **THEN** 存活检查通过、就绪检查失败且分类请求不被错误地标记为成功

### Requirement: Graceful external dependency degradation
外部 LLM 或非必要观测组件不可用时，系统 MUST 保持主分类和人工复核链路可用，并将需要辅助的请求降级到人工复核。

#### Scenario: LLM outage
- **WHEN** 外部 LLM 连续超时或熔断
- **THEN** 主模型继续服务，符合兜底条件的工单转为人工复核并记录降级指标

### Requirement: Retention and deletion
系统 SHALL 对原始文本、脱敏文本、模型输入输出和审计记录分别配置保留期限，并允许授权人员执行可审计的 PoC 数据清理。

#### Scenario: Retention expiry
- **WHEN** 一条 PoC 工单超过其配置的文本保留期限
- **THEN** 系统删除或不可逆匿名化其正文，同时保留最小化的审计证明
