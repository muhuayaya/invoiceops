## Why

人工复核详情卡片占用过多垂直空间，复核人员需要频繁滚动才能查看队列和提交决策。同时，复核完成后的状态没有可靠写回 PostgreSQL，管理员进入数据管理时仍可能看到工单处于待复核状态。

## What Changes

- 将人工复核页面调整为更紧凑的双栏工作区，限制详情区高度并缩短备注输入区域。
- 复核决策完成后，持久化工单状态为“已复核”。
- 增加 API 集成回归测试，验证管理员在不重启 API 的情况下能读取已复核状态。

## Capabilities

### New Capabilities

- `review-workbench-status-sync`: 为人工复核工作区和管理员状态查询提供可验证的界面与持久化行为。

### Modified Capabilities

无。

## Impact

- 前端：`apps/web/src/main.tsx`、`apps/web/src/styles.css`。
- 应用服务：复核决策状态持久化逻辑。
- 测试：PostgreSQL 适配器（使用 SQLite URL 的集成测试）和 API 复核流程。
- 部署：需要重新构建并重启 VM Compose 中的 API、Worker、Web 服务。
