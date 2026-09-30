from __future__ import annotations

import httpx
from pathlib import Path
import pytest

from apps.web.api_client import ApiClient, ApiError, LABELS


def test_api_client_sends_bearer_and_keeps_csv_bytes():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("download"):
            return httpx.Response(200, content=b"row_number,labels\n2,PAYMENT_STATUS\n", headers={"content-type": "text/csv"})
        return httpx.Response(200, json={"id": "batch-1"})

    client = ApiClient("http://api.test", transport=httpx.MockTransport(handler))
    try:
        assert client.request("POST", "/v1/batches", token="secret", files={"file": ("input.csv", b"text\nhello\n", "text/csv")}) == {"id": "batch-1"}
        assert client.request("GET", "/v1/batches/batch-1/download", token="secret").startswith(b"row_number")
    finally:
        client.close()
    assert all(request.headers["authorization"] == "Bearer secret" for request in requests)
    assert b"name=\"file\"" in requests[0].content
    assert len(LABELS) == 8


def test_api_client_preserves_auth_and_conflict_status():
    def handler(request: httpx.Request) -> httpx.Response:
        status = 401 if request.url.path.endswith("me") else 409
        return httpx.Response(status, json={"detail": "conflict"})

    client = ApiClient("http://api.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ApiError) as expired:
            client.request("GET", "/v1/auth/me", token="expired")
        with pytest.raises(ApiError) as conflict:
            client.request("POST", "/v1/reviews/ticket-1", token="reviewer", json={"expected_version": 1})
    finally:
        client.close()
    assert expired.value.status_code == 401
    assert conflict.value.status_code == 409


def test_streamlit_classification_and_role_navigation():
    from streamlit.testing.v1 import AppTest

    class StubApi:
        def __init__(self):
            self.user = {"id": "user-1", "email": "user@example.com", "role": "user", "active": True}

        def request(self, method, path, **kwargs):
            if path == "/v1/auth/me":
                return self.user
            if path == "/readyz":
                return {"status": "ready", "model_version": "xlmr-v1", "degraded": False}
            if method == "POST" and path == "/v1/classifications":
                return {
                    "ticket_id": "ticket-1",
                    "labels": ["PAYMENT_STATUS"],
                    "probabilities": {label: 0.9 if label == "PAYMENT_STATUS" else 0.1 for label in LABELS},
                    "review_status": "not_required",
                    "taxonomy_version": "invoiceops-v1",
                    "model_version": "xlmr-v1",
                    "threshold_version": "threshold-v1",
                    "degraded": False,
                }
            return []

    stub = StubApi()
    app = AppTest.from_file(Path(__file__).parents[1] / "apps/web/app.py", default_timeout=20)
    app.session_state["token"] = "session-token"
    app.session_state["api_client"] = stub
    app.run()
    assert not app.exception
    assert "管理员" not in app.radio[0].options
    app.text_area[0].set_value("Payment is pending")
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state["last_classification"]["ticket_id"] == "ticket-1"

    stub.user["role"] = "admin"
    app.run()
    assert not app.exception
    assert "管理员" in app.radio[0].options


def test_streamlit_clears_expired_session():
    from streamlit.testing.v1 import AppTest

    class ExpiredApi:
        def request(self, method, path, **kwargs):
            raise ApiError(401, "expired")

    app = AppTest.from_file(Path(__file__).parents[1] / "apps/web/app.py", default_timeout=20)
    app.session_state["token"] = "expired-token"
    app.session_state["api_client"] = ExpiredApi()
    app.run()
    assert not app.exception
    assert "token" not in app.session_state
    assert app.error


def test_streamlit_batch_progress_errors_and_downloads():
    from streamlit.testing.v1 import AppTest

    class BatchApi:
        def request(self, method, path, **kwargs):
            if path == "/v1/auth/me":
                return {"id": "user-1", "email": "user@example.com", "role": "user", "active": True}
            if path == "/readyz":
                return {"status": "ready", "degraded": False}
            if path == "/v1/batches":
                return [{"id": "batch-1", "status": "completed", "total": 2, "succeeded": 1, "failed": 1, "pending": 0}]
            if path == "/v1/batches/batch-1":
                return {"id": "batch-1", "status": "completed", "total": 2, "succeeded": 1, "failed": 1, "pending": 0}
            if path.endswith("/errors"):
                return b"row_number,external_id,error_code,error_reason\n3,ext-2,invalid_text,Text required\n"
            if path.endswith("/download"):
                return b"row_number,labels,model_version,degraded,error_reason\n2,PAYMENT_STATUS,xlmr-v1,false,\n3,,,,Text required\n"
            return []

    app = AppTest.from_file(Path(__file__).parents[1] / "apps/web/app.py", default_timeout=20)
    app.session_state["token"] = "session-token"
    app.session_state["api_client"] = BatchApi()
    app.run()
    app.radio[0].set_value(app.radio[0].options[1]).run()

    assert not app.exception
    assert len(app.get("progress")) == 1
    assert len(app.get("download_button")) == 2
    assert any("invalid_text" in str(table.value) for table in app.dataframe)


def test_streamlit_review_submits_versioned_decision():
    from streamlit.testing.v1 import AppTest

    class ReviewApi:
        def __init__(self):
            self.submission = None

        def request(self, method, path, **kwargs):
            if path == "/v1/auth/me":
                return {"id": "reviewer-1", "email": "reviewer@example.com", "role": "user", "active": True}
            if path == "/v1/reviews/queue":
                return [{"ticket_id": "ticket-2", "state_version": 7, "labels": ["PAYMENT_STATUS"], "text": "Payment pending", "review_reasons": ["high_risk"]}]
            if path == "/v1/reviews/ticket-2" and method == "POST":
                self.submission = kwargs["json"]
                return {"decision": "confirmed"}
            return []

    stub = ReviewApi()
    app = AppTest.from_file(Path(__file__).parents[1] / "apps/web/app.py", default_timeout=20)
    app.session_state["token"] = "session-token"
    app.session_state["api_client"] = stub
    app.run()
    app.radio[0].set_value(app.radio[0].options[3]).run()
    next(button for button in app.button if button.label == "提交复核").click().run()

    assert not app.exception
    assert stub.submission == {
        "expected_version": 7,
        "decision": "confirmed",
        "final_labels": ["PAYMENT_STATUS"],
        "comment": "",
    }


def test_streamlit_admin_renders_independent_histories():
    from streamlit.testing.v1 import AppTest

    class AdminApi:
        def __init__(self):
            self.paths = []
            self.calls = []
        def request(self, method, path, **kwargs):
            self.paths.append(path)
            self.calls.append((method, path, kwargs))
            if method == "DELETE":
                return {"deleted": kwargs["json"]["ids"], "count": len(kwargs["json"]["ids"])}
            if path == "/v1/auth/me":
                return {"id": "admin-1", "email": "admin@example.com", "role": "admin", "active": True}
            if path == "/readyz":
                return {"status": "ready", "degraded": False}
            if path == "/v1/admin/users":
                return [{"id": "user-1", "email": "user@example.com", "role": "user", "active": True}]
            if path == "/v1/admin/tickets":
                return [
                    {"ticket_id": "ticket-1", "prediction_id": "prediction-1", "labels": ["PAYMENT_STATUS"], "review_status": "confirmed"},
                    {"ticket_id": "ticket-2", "review_status": "pending_reclassification", "redacted_text": "Pending text"},
                ]
            if path == "/v1/admin/batches":
                return [{"id": "batch-1", "name": "June", "status": "completed"}]
            if path == "/v1/admin/history":
                return {
                    "prediction_history": [{"prediction_id": "prediction-1"}],
                    "review_decisions": [{"decision": "corrected"}],
                    "batch_item_history": [{"batch_id": "batch-deleted", "row_number": 3}],
                    "audit_events": [{"action": "batch.delete"}],
                }
            return []

    app = AppTest.from_file(Path(__file__).parents[1] / "apps/web/app.py", default_timeout=20)
    app.session_state["token"] = "admin-token"
    app.session_state["api_client"] = AdminApi()
    app.run()
    app.radio[0].set_value(app.radio[0].options[4]).run()

    assert not app.exception
    assert {"/v1/admin/users", "/v1/admin/tickets", "/v1/admin/batches", "/v1/admin/history"}.issubset(set(app.session_state["api_client"].paths))
    assert {"选择要批量删除的工单", "选择要批量删除的分类记录", "选择要批量删除的批次"}.issubset({item.label for item in app.multiselect})
    next(item for item in app.multiselect if item.label == "选择要批量删除的工单").set_value(["ticket-1"]).run()
    next(item for item in app.checkbox if "确认物理删除所选" in item.label).check().run()
    next(item for item in app.button if item.label == "批量删除所选工单").click().run()
    assert any(method == "DELETE" and path == "/v1/admin/tickets" and call["json"]["ids"] == ["ticket-1"] for method, path, call in app.session_state["api_client"].calls)
    assert not any(item.label == "重新分类" for item in app.button)
    ticket_select = next(item for item in app.selectbox if item.label == "选择工单")
    ticket_select.select_index(ticket_select.options.index("ticket-2")).run()
    next(item for item in app.button if item.label == "重新分类").click().run()
    assert not app.exception
    assert any(method == "POST" and path == "/v1/admin/tickets/ticket-2/reclassify" for method, path, _call in app.session_state["api_client"].calls)


def test_streamlit_batch_page_surfaces_review_items_and_queue_navigation():
    from streamlit.testing.v1 import AppTest

    class BatchReviewApi:
        def __init__(self):
            self.paths = []

        def request(self, method, path, **kwargs):
            self.paths.append(path)
            if path == "/v1/auth/me":
                return {"id": "user-1", "email": "user@example.com", "role": "user", "active": True}
            if path == "/readyz":
                return {"status": "ready", "degraded": False}
            if path == "/v1/batches":
                return [{"id": "batch-1", "status": "completed", "total": 1, "succeeded": 1, "failed": 0, "pending": 0}]
            if path == "/v1/batches/batch-1":
                return {"id": "batch-1", "status": "completed", "total": 1, "succeeded": 1, "failed": 0, "pending": 0}
            if path.endswith("/errors"):
                return b"row_number,error_code,error_reason\n"
            if path.endswith("/download"):
                return b"row_number,external_id,labels,review_status,review_reasons\n2,invoice-2,SUPPLIER_MASTER_CHANGE,pending_review,supplier_master_change|low_confidence\n"
            if path == "/v1/reviews/queue":
                return []
            return []

    api = BatchReviewApi()
    app = AppTest.from_file(Path(__file__).parents[1] / "apps/web/app.py", default_timeout=20)
    app.session_state["token"] = "session-token"
    app.session_state["api_client"] = api
    app.run()
    app.radio[0].set_value("CSV 批量").run()

    assert not app.exception
    assert any("1 条记录需要人工复核" in element.value for element in app.warning)
    assert any("低置信度" in str(frame.value) for frame in app.dataframe)
    next(button for button in app.button if button.label == "打开待复核工单").click().run()
    assert not app.exception
    assert "待复核工单" in [header.value for header in app.header]
    assert "/v1/reviews/queue" in api.paths


def test_streamlit_classification_shows_degraded_and_unavailable_states():
    from streamlit.testing.v1 import AppTest

    class ModelApi:
        def __init__(self, unavailable=False):
            self.unavailable = unavailable

        def request(self, method, path, **kwargs):
            if path == "/v1/auth/me":
                return {"id": "user-1", "email": "user@example.com", "role": "user", "active": True}
            if path == "/readyz":
                if self.unavailable:
                    raise ApiError(503, "not ready")
                return {"status": "degraded", "model_version": "tfidf-v1", "degraded": True}
            if method == "POST" and path == "/v1/classifications":
                return {
                    "ticket_id": "ticket-1",
                    "labels": ["PAYMENT_STATUS"],
                    "probabilities": {label: 0.9 if label == "PAYMENT_STATUS" else 0.1 for label in LABELS},
                    "review_status": "pending_review",
                    "taxonomy_version": "invoiceops-v1",
                    "model_version": "tfidf-v1",
                    "threshold_version": "threshold-v1",
                    "degraded": True,
                    "fallback_reason": "primary_unavailable",
                }
            return []

    unavailable = AppTest.from_file(Path(__file__).parents[1] / "apps/web/app.py", default_timeout=20)
    unavailable.session_state["token"] = "session-token"
    unavailable.session_state["api_client"] = ModelApi(unavailable=True)
    unavailable.run()
    assert not unavailable.exception
    assert unavailable.error

    degraded = AppTest.from_file(Path(__file__).parents[1] / "apps/web/app.py", default_timeout=20)
    degraded.session_state["token"] = "session-token"
    degraded.session_state["api_client"] = ModelApi()
    degraded.run()
    degraded.text_area[0].set_value("Please release this payment")
    degraded.button[0].click().run()
    assert not degraded.exception
    assert len(degraded.warning) >= 2


def test_auth_cookie_component_speaks_streamlit_protocol():
    script = (Path(__file__).parents[1] / "apps/web/auth_cookie/index.js").read_text(encoding="utf-8")
    assert 'send("streamlit:componentReady", {apiVersion: 1})' in script
    assert "streamlit:setComponentReady" not in script
    assert 'send("streamlit:setFrameHeight", {height: 0})' in script
    assert 'send("streamlit:setComponentValue", {value, dataType: "json"})' in script


def test_session_token_is_restored_from_cookie_unless_discarded():
    from apps.web.app import AUTH_COOKIE_NAME, token_from_cookies

    assert token_from_cookies({AUTH_COOKIE_NAME: "saved-token"}) == "saved-token"
    assert token_from_cookies({AUTH_COOKIE_NAME: "saved-token"}, {"saved-token"}) is None
    assert token_from_cookies({AUTH_COOKIE_NAME: ""}) is None
    assert token_from_cookies({}) is None


def test_streamlit_login_persists_cookie_and_logout_discards_it():
    from streamlit.testing.v1 import AppTest

    class LoginApi:
        def __init__(self):
            self.calls = []

        def request(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs.get("token")))
            if path == "/v1/auth/login":
                return {"token": "fresh-token", "user": {"id": "user-1", "email": "user@example.com", "role": "user", "active": True}}
            if path == "/v1/auth/me":
                return {"id": "user-1", "email": "user@example.com", "role": "user", "active": True}
            if path == "/readyz":
                return {"status": "ready", "degraded": False}
            if path == "/v1/auth/logout":
                return {"revoked": True}
            return []

    api = LoginApi()
    app = AppTest.from_file(Path(__file__).parents[1] / "apps/web/app.py", default_timeout=20)
    app.session_state["api_client"] = api
    app.run()
    app.text_input[0].input("user@example.com")
    app.text_input[1].input("UserPassword123")
    app.button[0].click().run()

    assert not app.exception
    assert app.session_state["token"] == "fresh-token"
    assert app.session_state["auth_cookie_op"]["action"] == "set"
    assert app.session_state["auth_cookie_op"]["value"] == "fresh-token"
    assert ("GET", "/v1/auth/me", "fresh-token") in api.calls
    next(button for button in app.button if button.label == "退出登录").click().run()

    assert not app.exception
    assert "token" not in app.session_state
    assert "fresh-token" in app.session_state["revoked_tokens"]
    assert app.session_state["auth_cookie_op"]["action"] == "remove"
    assert any(tab.label == "登录" for tab in app.tabs)


def test_console_display_helpers_translate_business_fields():
    from apps.web import ui

    assert set(ui.LABEL_NAMES) == set(LABELS)
    shown = ui.display_rows([{
        "review_status": "pending_review",
        "labels": ["SUPPLIER_MASTER_CHANGE", "PAYMENT_STATUS"],
        "review_reasons": "supplier_master_change|low_confidence",
        "role": "admin",
        "active": False,
        "degraded": "false",
        "created_at": "2026-09-30T00:30:00+00:00",
        "error_code": "invalid_text",
    }])
    assert shown == [{
        "复核状态": "待复核",
        "模型标签": "供应商主数据变更、付款状态查询",
        "复核原因": "高风险：供应商主数据变更、低置信度：预测概率接近分类阈值",
        "角色": "管理员",
        "账号状态": "停用",
        "备用模型": "否",
        "时间": "2026-09-30 08:30",
        "错误代码": "invalid_text",
    }]
    assert ":red-badge[供应商主数据变更]" in ui.label_badges(["SUPPLIER_MASTER_CHANGE"])
    assert ui.review_badge("pending_reclassification") == ":violet-badge[待重新分类]"


def test_console_theme_is_configured_and_shipped_in_images():
    import tomllib

    root = Path(__file__).parents[1]
    config = tomllib.loads((root / ".streamlit/config.toml").read_text(encoding="utf-8"))
    assert config["theme"]["base"] == "light"
    assert config["theme"]["sidebar"]["backgroundColor"]
    assert config["client"]["toolbarMode"] == "minimal"
    for dockerfile in ("Dockerfile", "Dockerfile.runtime-patch"):
        assert "COPY .streamlit ./.streamlit" in (root / dockerfile).read_text(encoding="utf-8")


def test_web_password_rule_matches_server_rule():
    from apps.web.ui import password_problem
    from invoiceops.security.auth import password_is_valid

    samples = ["Abc123def456", "A1" * 32, "abcdefghijkl", "123456789012", "Abc123def45", "A1" * 32 + "b",
               "Abc 123def456", "Abc123def456!", "Abc_123def456", "Abc123def\u4e2d456", "Abc\uff11\uff12\uff13def456", ""]
    for sample in samples:
        assert (password_problem(sample) is None) == password_is_valid(sample), sample
    assert "12–64" in password_problem("Abc123")
    assert "空格" in password_problem("Abc 123def456")
    assert "同时包含" in password_problem("abcdefghijkl")


def test_streamlit_register_rejects_weak_password_before_calling_api():
    from streamlit.testing.v1 import AppTest

    class RegisterApi:
        def __init__(self):
            self.paths = []

        def request(self, method, path, **kwargs):
            self.paths.append(path)
            return []

    api = RegisterApi()
    app = AppTest.from_file(Path(__file__).parents[1] / "apps/web/app.py", default_timeout=20)
    app.session_state["api_client"] = api
    app.run()
    next(item for item in app.text_input if item.label == "注册邮箱").input("new@example.com")
    next(item for item in app.text_input if item.label == "设置密码").input("weak password!")
    next(button for button in app.button if button.label == "注册并登录").click().run()

    assert not app.exception
    assert any("只能包含英文字母和数字" in element.value for element in app.error)
    assert "/v1/auth/register" not in api.paths
