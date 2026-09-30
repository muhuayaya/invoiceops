# infra/migrations

Alembic 数据库迁移。

| 文件 | 说明 |
|---|---|
| `alembic.ini` | Alembic 配置 |
| `env.py` | 迁移环境，从环境变量 `INVOICEOPS_DATABASE_URL` 读取数据库地址 |
| `script.py.mako` | 新迁移文件的模板 |
| [`versions/`](versions/) | 各版本迁移脚本 |

## 使用

```powershell
$env:INVOICEOPS_DATABASE_URL = "postgresql+psycopg://invoiceops:<密码>@localhost:5432/invoiceops"
uv run alembic -c infra/migrations/alembic.ini upgrade head
```

Docker 部署时，API 容器启动前会自动执行 `upgrade head`。
