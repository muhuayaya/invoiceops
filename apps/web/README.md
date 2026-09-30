# apps/web

Streamlit 工作台。页面不直接访问数据库，所有读写都通过 FastAPI 接口完成。

| 文件 | 说明 |
|---|---|
| `app.py` | 页面主程序：登录注册、单条分类、CSV 批量、我的历史、待复核工单、管理员后台 |
| `ui.py` | 展示辅助函数：标签和状态的中文名称、徽标、时间格式、页面样式；前端密码规则检查（与服务端一致） |
| `api_client.py` | 调用 FastAPI 的 HTTP 客户端，自动携带登录令牌 |
| [`auth_cookie/`](auth_cookie/) | 保存登录会话 Cookie 的前端组件，刷新浏览器后可恢复登录 |

页面主题配置在根目录 `.streamlit/config.toml`。

## 启动

```powershell
$env:INVOICEOPS_API_URL = "http://127.0.0.1:8000"
uv run streamlit run apps/web/app.py
```
