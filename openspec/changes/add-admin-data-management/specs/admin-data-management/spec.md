## Purpose

为管理员提供一个安全、可追踪的工单数据入口，用于手工录入、检索和核对试点数据，同时复用现有分类、脱敏、幂等和审计能力。

## ADDED Requirements

### Requirement: Administrator-only data access

数据管理能力 MUST 只允许已认证的管理员使用。观察者、复核员和匿名调用方访问数据管理接口时 MUST 被拒绝，并返回结构化授权错误；网页端 MUST 不向非管理员展示数据管理入口。

#### Scenario: Reviewer attempts to access data management

- **WHEN** 复核员请求数据管理页面或接口
- **THEN** 页面不展示数据管理入口，接口返回 403 且响应包含错误码和追踪编号

#### Scenario: Anonymous caller attempts to access data management

- **WHEN** 未携带有效令牌的调用方请求数据管理接口
- **THEN** 接口返回 401 且记录授权失败审计事件

### Requirement: Manual ticket entry

管理员 MUST 能够录入请求编号、来源和工单正文，并提交标签体系版本和幂等键。录入 MUST 复用现有字段校验、敏感信息脱敏、分类、路由和审计链路；同一幂等键和请求体重复提交不得创建重复业务记录。

#### Scenario: Administrator enters a valid ticket

- **WHEN** 管理员提交有效工单数据和幂等键
- **THEN** 系统创建一条经过脱敏和分类的工单，返回分类结果、路由、版本和追踪编号，并产生审计事件

#### Scenario: Administrator submits invalid ticket data

- **WHEN** 管理员提交空正文、非法来源、未知标签体系版本或重复请求编号
- **THEN** 系统返回结构化校验错误且不创建新的业务记录

### Requirement: Ticket data listing and filtering

管理员 MUST 能够查看已录入工单的最小化数据摘要，包括请求编号、来源、状态、风险、语言、分类标签、责任队列和创建时间，并能按请求编号、风险和状态筛选。

#### Scenario: Administrator filters entered tickets

- **WHEN** 管理员按请求编号、风险或状态查询数据管理列表
- **THEN** 系统只返回符合筛选条件的已录入工单，并按创建时间倒序排列

#### Scenario: Stored content is minimized

- **WHEN** 管理员查看工单摘要
- **THEN** 页面和接口只展示已脱敏正文，不返回原始敏感信息或凭据

### Requirement: Controlled deletion and batch overwrite

数据管理模块 MUST 只允许管理员执行单条删除、批量删除和批量覆盖。删除或覆盖接口 MUST 要求幂等键，并在执行前完成全部输入校验；批量操作遇到不存在的目标或非法数据时 MUST 不开始变更。历史审计事件 MUST 保持追加式保存，删除和覆盖必须记录操作者、原工单标识和新工单标识（如适用）。

#### Scenario: Administrator deletes selected tickets

- **WHEN** 管理员确认删除一条或多条已录入工单
- **THEN** 当前工单数据被删除，接口返回删除数量，历史审计事件保留，并产生删除审计事件

#### Scenario: Non-admin cannot mutate data

- **WHEN** 观察者、复核员或匿名调用方请求删除或批量覆盖
- **THEN** 接口返回结构化 401/403 错误，不修改工单数据，并记录授权失败审计（匿名请求）

#### Scenario: Administrator batch-overwrites tickets

- **WHEN** 管理员上传包含既有 `request_id`、来源、正文和标签体系版本的覆盖数据
- **THEN** 系统按 `request_id` 替换目标工单，重新执行脱敏、分类和路由，返回每条新分类结果，并追加覆盖审计事件

#### Scenario: Invalid batch operation is rejected before mutation

- **WHEN** 批量删除或批量覆盖中存在不存在的目标、重复请求编号、非法来源或未知标签体系版本
- **THEN** 系统返回结构化错误且整批不发生变更

#### Scenario: Replayed mutation is idempotent

- **WHEN** 管理员使用相同幂等键和相同操作体重复提交删除或批量覆盖
- **THEN** 系统返回首次操作结果且不重复删除、创建或分类；相同幂等键搭配不同操作体返回冲突
