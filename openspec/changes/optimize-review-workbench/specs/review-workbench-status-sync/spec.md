## Purpose

让复核人员可以在有限的屏幕空间内连续处理工单，并让管理员可靠查看复核后的最终工单状态。

## ADDED Requirements

### Requirement: Review workspace is compact and usable

人工复核页面 SHALL 在桌面布局中同时展示待复核队列和当前工单详情，详情区域 SHALL 采用受约束的可滚动高度，且备注输入区域 SHALL 不得默认占用大块屏幕空间。窄屏布局 SHALL 退化为单栏显示。

#### Scenario: Desktop reviewer works in two panes

- **WHEN** reviewer opens the human review page on a desktop-sized viewport
- **THEN** the review queue and selected ticket details are visible as two compact columns, and the detail panel does not force the whole page to grow beyond the viewport without an internal scroll area

#### Scenario: Narrow viewport remains operable

- **WHEN** reviewer opens the human review page on a narrow viewport
- **THEN** the queue and detail panel are stacked in one column and all decision controls remain reachable

### Requirement: Reviewed status is persisted and visible to administrators

当 reviewer successfully submits a review decision for a ticket in `needs_review` status, the system SHALL persist that ticket as `reviewed`. A subsequent administrator ticket-list request, including a request made by the same API process without restart, SHALL return the ticket with status `reviewed`, the latest reviewed labels and primary queue, and the review queue SHALL no longer return it as pending.

#### Scenario: Review updates durable ticket status

- **WHEN** a reviewer submits a valid decision for a pending ticket
- **THEN** the decision is stored, the ticket status is durably changed to `reviewed`, and the API returns a successful decision response

#### Scenario: Admin sees reviewed ticket after refresh

- **WHEN** an administrator requests the ticket list after the reviewer decision
- **THEN** the ticket appears with status `reviewed` and the labels and primary queue from the latest review decision, including when the API process was not restarted between the two requests

#### Scenario: Reviewed ticket leaves pending queue

- **WHEN** the review queue is requested after a successful decision
- **THEN** that ticket is not returned as a pending review item
