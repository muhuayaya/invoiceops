## Purpose

保证异步 worker 写入的工单数据在 PostgreSQL 提交后能够被 API 读接口及时看到，避免用户必须重启服务才能使用复核、数据管理、审计和指标功能。

## ADDED Requirements

### Requirement: API reads reflect committed worker writes

当独立 worker 已将工单及其预测结果提交到 PostgreSQL 后，API 的工单读取接口 MUST 在服务不重启的情况下返回这些数据，并保持现有角色权限和响应结构不变。

#### Scenario: Newly processed batch appears in administrator data management

- **WHEN** worker completes a batch row and commits its ticket and prediction to PostgreSQL
- **THEN** an authenticated administrator's next data-management list request returns that ticket
- **AND** the response includes its request identifier, risk, status, labels, and primary queue

#### Scenario: Newly processed reviewable ticket appears in review queue

- **WHEN** worker completes a batch row whose prediction requires manual review
- **THEN** an authenticated reviewer or observer's next review-list request returns that ticket
- **AND** the ticket remains eligible for the existing review workflow

#### Scenario: Newly processed ticket appears in audit and metrics reads

- **WHEN** worker commits the ticket's classification and audit events
- **THEN** an authorized audit request for that request identifier returns the committed events
- **AND** the metrics response reflects the committed ticket and audit counts

#### Scenario: Existing authorization and data rules remain enforced

- **WHEN** a user without the required role requests refreshed data
- **THEN** the API returns the same authorization error as before
- **AND** refreshing the read state does not expose unsanitized ticket text or delete audit history
