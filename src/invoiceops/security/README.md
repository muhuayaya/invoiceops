# src/invoiceops/security

账号、会话与权限。

| 文件 | 说明 |
|---|---|
| `auth.py` | 注册、登录、退出、令牌校验；管理员新建用户、修改角色、停用和删除用户；初始化第一个管理员 |
| `authorization.py` | 权限规则：`require_admin`、`can_view_ticket`、`can_download_batch`、`can_review` |

- 密码使用 Argon2 哈希；登录令牌只返回一次，数据库只保存摘要，会话有效期 12 小时。
- 新密码须为 12–64 位英文字母和数字组合，且至少各含一个字母和一个数字。
- 自助注册一律为普通用户；系统至少保留一名有效管理员。
- 普通用户只能查看自己的工单和批次；所有有效用户都可以复核待复核工单。

## 手动创建管理员

```powershell
uv run python -m invoiceops.security.auth --email admin@example.local
```
