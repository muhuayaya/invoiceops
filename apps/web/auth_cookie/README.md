# apps/web/auth_cookie

Streamlit 自定义组件，用于在浏览器中读写登录会话 Cookie（`invoiceops_session`）。

| 文件 | 说明 |
|---|---|
| `index.html` | 组件页面，加载 `index.js` |
| `index.js` | 与 Streamlit 通信，写入或清除 Cookie；HTTPS 下自动加 `Secure` 标记 |

由 `apps/web/app.py` 通过 `components.declare_component` 加载。登录成功后写入 Cookie，退出或会话失效时清除。
