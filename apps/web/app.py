"""Streamlit UI. All business reads and writes go through FastAPI."""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from apps.web.api_client import ApiClient, ApiError, LABELS
from apps.web.ui import (
    HIGH_RISK_LABELS,
    ROLES,
    badge,
    batch_badge,
    batch_state_text,
    brand_html,
    display_rows,
    inject_css,
    label_badges,
    label_name,
    labels_text,
    page_header,
    review_badge,
    review_reasons_text,
    review_state_text,
    short_id,
    user_html,
)
from apps.web.ui import PASSWORD_HINT, password_problem


st.set_page_config(page_title="InvoiceOps 发票工单运营台", page_icon=":material/receipt_long:", layout="wide", initial_sidebar_state="expanded")
auth_cookie = components.declare_component("invoiceops_auth_cookie", path=str(Path(__file__).parent / "auth_cookie"))
AUTH_COOKIE_NAME = "invoiceops_session"


def token_from_cookies(cookies: Any, revoked: Any = ()) -> str | None:
    """Return the persisted session token unless this browser session already discarded it."""
    try:
        token = cookies.get(AUTH_COOKIE_NAME)
    except Exception:
        return None
    return token if isinstance(token, str) and token and token not in revoked else None


def queue_auth_cookie(action: str, value: str | None = None) -> None:
    """Queue a browser cookie write; sync_auth_cookie renders it until the component confirms."""
    seq = st.session_state.get("auth_cookie_seq", 0) + 1
    st.session_state.auth_cookie_seq = seq
    st.session_state.auth_cookie_op = {"action": action, "value": value, "seq": seq}


def sync_auth_cookie() -> None:
    op = st.session_state.get("auth_cookie_op")
    if not op:
        return
    result = auth_cookie(action=op["action"], value=op["value"], key=f"auth_cookie_op_{op['seq']}", default=None)
    if result in ("saved", "removed"):
        st.session_state.pop("auth_cookie_op", None)


def restore_session() -> None:
    """Restore the login from the cookie sent with the websocket handshake (first run, no iframe round trip)."""
    if st.session_state.get("token"):
        return
    try:
        cookies = st.context.cookies
    except Exception:
        return
    token = token_from_cookies(cookies, st.session_state.get("revoked_tokens", set()))
    if token:
        st.session_state.token = token


def start_session(result: dict[str, Any]) -> None:
    st.session_state.token = result["token"]
    st.session_state.user = result["user"]
    queue_auth_cookie("set", result["token"])


def forget_session() -> None:
    token = st.session_state.pop("token", None)
    if token:
        revoked = set(st.session_state.get("revoked_tokens", set()))
        revoked.add(token)
        st.session_state.revoked_tokens = revoked
    st.session_state.pop("user", None)
    st.session_state.pop("last_classification", None)
    queue_auth_cookie("remove")


def api() -> ApiClient:
    if "api_client" not in st.session_state:
        st.session_state.api_client = ApiClient()
    return st.session_state.api_client


def call(method: str, path: str, **kwargs: Any) -> Any:
    try:
        return api().request(method, path, token=st.session_state.get("token"), **kwargs)
    except ApiError as exc:
        if exc.status_code == 401 and st.session_state.get("token"):
            forget_session()
            st.session_state.auth_notice = "会话已过期或账号已停用，请重新登录。"
            st.rerun()
        if exc.status_code == 409 and path.startswith("/v1/reviews/"):
            st.error("记录已由其他人更新，请刷新页面后重试。")
        elif exc.status_code == 503:
            st.error("分类服务当前不可用，请稍后重试。")
        elif exc.status_code == 403:
            st.error(f"没有权限执行此操作：{exc}")
        else:
            st.error(str(exc))
        return None


def rows(payload: Any) -> list[dict[str, Any]]:
    return payload if isinstance(payload, list) else []


def navigate_to(page: str) -> None:
    st.session_state.nav_page = page


def as_table(data: list[dict[str, Any]], *, columns: list[str] | None = None, empty: str = "暂无记录。", height: int | None = None) -> None:
    if data:
        options: dict[str, Any] = {"width": "stretch", "hide_index": True}
        if height:
            options["height"] = height
        st.dataframe(display_rows(data, columns), **options)
    else:
        st.info(empty)


def metric_row(items: list[tuple[str, Any]], *, help_texts: dict[str, str] | None = None) -> None:
    for column, (label, value) in zip(st.columns(len(items)), items):
        column.metric(label, value, border=True, help=(help_texts or {}).get(label))


def model_notice(item: dict[str, Any]) -> None:
    if item.get("degraded"):
        st.warning(f"当前使用已批准的备用模型：{item.get('model_version', '—')}。原因：{item.get('fallback_reason') or '主模型不可用'}")
    else:
        st.caption(f"实际模型：{item.get('model_version') or '—'} · 阈值版本：{item.get('threshold_version') or '—'}")


def readiness_status() -> tuple[str, dict[str, Any]]:
    """Return (state, payload) where state is ready / degraded / unavailable."""
    try:
        payload = api().request("GET", "/readyz")
    except ApiError:
        return "unavailable", {}
    if not isinstance(payload, dict):
        return "unavailable", {}
    return ("degraded" if payload.get("degraded") else "ready"), payload


def sign_in() -> None:
    inject_css()
    left, right = st.columns([1.1, 1], gap="large")
    with right:
        st.title("InvoiceOps 工作台")
        notice = st.session_state.pop("auth_notice", None)
        if notice:
            st.error(notice)
        login, register = st.tabs(["登录", "注册"])
        with login:
            with st.form("login"):
                email = st.text_input("邮箱", autocomplete="email")
                password = st.text_input("密码", type="password", autocomplete="current-password")
                submitted = st.form_submit_button("登录", type="primary", width="stretch")
            if submitted:
                result = call("POST", "/v1/auth/login", json={"email": email.strip(), "password": password})
                if result:
                    start_session(result)
                    st.rerun()
        with register:
            st.caption("新账号自动成为普通用户。")
            with st.form("register"):
                email = st.text_input("注册邮箱", autocomplete="email")
                password = st.text_input("设置密码", type="password", autocomplete="new-password", help=PASSWORD_HINT)
                st.caption(PASSWORD_HINT)
                submitted = st.form_submit_button("注册并登录", width="stretch")
            problem = password_problem(password) if submitted else None
            if problem:
                st.error(problem)
            elif submitted:
                created = call("POST", "/v1/auth/register", json={"email": email.strip(), "password": password})
                if created:
                    result = call("POST", "/v1/auth/login", json={"email": email.strip(), "password": password})
                    if result:
                        start_session(result)
                        st.rerun()
    with left:
        st.html('<div class="io-login-hero">' + brand_html("应付账款发票工单运营台") + "</div>")
        for icon, text in (
            (":material/bolt:", "工单智能分类：8 类发票与付款问题，给出各类置信度"),
            (":material/fact_check:", "人工复核闭环：低置信度与供应商主数据变更自动进入复核队列"),
            (":material/table_view:", "CSV 批量处理：进度跟踪、错误清单与完整结果下载"),
            (":material/history:", "全程留痕：原预测、人工决定与审计事件独立保存"),
        ):
            st.markdown(f"{icon} {text}")


def sidebar(user: dict[str, Any], state: str, readiness: dict[str, Any]) -> str:
    with st.sidebar:
        st.html(brand_html())
        role = user.get("role")
        st.html(user_html(user.get("email")))
        st.markdown(badge(ROLES.get(role, role or "—"), "violet" if role == "admin" else "gray"))
        st.html('<div class="io-section">工作区</div>')
        options = ["单条分类", "CSV 批量", "我的历史", "待复核工单"]
        if role == "admin":
            options.append("管理员")
        page = st.radio("导航", options, key="nav_page", label_visibility="collapsed")
        st.html('<div class="io-section">分类服务</div>')
        if state == "ready":
            st.markdown(badge("运行正常", "green") + f"  \n模型 `{readiness.get('model_version') or '—'}`")
        elif state == "degraded":
            st.markdown(badge("备用模型", "orange") + f"  \n模型 `{readiness.get('model_version') or '—'}`")
        else:
            st.markdown(badge("不可用", "red"))
        if readiness.get("demo_mode"):
            st.caption("开源数据，仅供参考。")
        st.divider()
        if st.button("退出登录", icon=":material/logout:", width="stretch"):
            call("POST", "/v1/auth/logout")
            forget_session()
            st.rerun()
    return page


def classify_page(state: str, readiness: dict[str, Any]) -> None:
    page_header("单条分类", "粘贴供应商邮件或付款工单文本，模型识别问题类别，并判断是否需要人工复核。")
    if state == "unavailable":
        st.error("分类服务当前不可用：主模型及已批准备用模型均不能提供分类。")
    elif readiness.get("demo_mode"):
        st.info("当前模型已通过制品哈希校验，项目数据为网上开源获取，仅供参考。")
    if state == "degraded":
        st.warning(f"主模型不可用，当前由已批准的备用模型 {readiness.get('model_version', '—')} 服务。")
    form_col, result_col = st.columns([1, 1.15], gap="large")
    with form_col:
        with st.form("classify"):
            text = st.text_area("发票或付款工单文本", height=220, placeholder="例如：供应商来信要求变更收款银行账户，请尽快更新……")
            external_id = st.text_input("外部编号（可选）", placeholder="如 ERP 单号、邮件编号")
            submitted = st.form_submit_button("提交分类", type="primary", icon=":material/bolt:", width="stretch")
        if submitted:
            if not text.strip():
                st.error("请输入工单文本。")
            else:
                payload = {"text": text.strip(), "source": "manual"}
                if external_id.strip():
                    payload["external_id"] = external_id.strip()
                result = call("POST", "/v1/classifications", json=payload)
                if result:
                    st.session_state.last_classification = result
    with result_col:
        result = st.session_state.get("last_classification")
        if not result:
            with st.container(border=True):
                st.subheader("分类建议")
                st.caption("提交工单文本后，这里显示命中类别、各类置信度和复核建议。")
            return
        probabilities = result.get("probabilities") or {}
        labels = result.get("labels") or []
        with st.container(border=True):
            st.subheader("分类建议")
            metric_row([
                ("命中类别", len(labels)),
                ("最高置信度", f"{max(probabilities.values()):.0%}" if probabilities else "—"),
                ("复核建议", review_state_text(result.get("review_status"))),
            ])
            st.markdown("**命中类别** " + label_badges(labels))
            if result.get("review_reasons"):
                st.markdown("**复核原因** " + review_reasons_text(result["review_reasons"]))
            ranked = sorted(LABELS, key=lambda label: probabilities.get(label) or 0, reverse=True)
            st.dataframe(
                [{"类别": label_name(label), "置信度": probabilities.get(label) or 0.0} for label in ranked],
                width="stretch",
                hide_index=True,
                column_config={"置信度": st.column_config.ProgressColumn("置信度", min_value=0.0, max_value=1.0, format="percent")},
            )
            st.caption(f"工单 ID：{result.get('ticket_id', '—')} · 标签版本：{result.get('taxonomy_version') or '—'}")
            model_notice(result)


def batch_page() -> None:
    page_header("CSV 批量分类", "批量导入发票工单，后台逐行分类；需复核的行会进入待复核队列。")
    limit = os.getenv("INVOICEOPS_MAX_UPLOAD_MB")
    with st.container(border=True):
        st.caption("上传 UTF-8 CSV。必需表头：text；可选表头：external_id。" + (f"文件上限：{limit} MB。" if limit else "具体文件大小上限由服务端配置。"))
        upload = st.file_uploader("选择 CSV 文件", type="csv")
        if st.button("上传并处理", type="primary", icon=":material/upload:", disabled=upload is None):
            result = call("POST", "/v1/batches", files={"file": (upload.name, upload.getvalue(), "text/csv")})
            if result:
                st.session_state.selected_batch = result.get("id") or result.get("batch_id")
                st.success("批次已创建。")
    batch_list = rows(call("GET", "/v1/batches"))
    if not batch_list:
        st.info("尚无批次。上传第一份 CSV 开始批量分类。")
        return
    metric_row([
        ("批次数", len(batch_list)),
        ("进行中", sum(1 for item in batch_list if item.get("status") in ("pending", "running", "paused"))),
        ("累计行数", sum(item.get("total") or 0 for item in batch_list)),
        ("失败行", sum(item.get("failed") or 0 for item in batch_list)),
    ])
    overview = []
    for item in batch_list:
        total = item.get("total") or 0
        done = (item.get("succeeded") or 0) + (item.get("failed") or 0)
        overview.append({
            "批次": item.get("name") or short_id(item.get("id")),
            "状态": batch_state_text(item.get("status")),
            "完成度": done / total if total else 0.0,
            "总行数": total,
            "成功": item.get("succeeded") or 0,
            "失败": item.get("failed") or 0,
            "待处理": item.get("pending") or 0,
            "批次 ID": item.get("id"),
        })
    st.dataframe(overview, width="stretch", hide_index=True, column_config={"完成度": st.column_config.ProgressColumn("完成度", min_value=0.0, max_value=1.0, format="percent")})
    by_id = {str(item.get("id") or item.get("batch_id")): item for item in batch_list}
    ids = list(by_id)
    pick, refresh = st.columns([4, 1], vertical_alignment="bottom")
    selected = pick.selectbox(
        "查看批次",
        ids,
        index=ids.index(st.session_state.selected_batch) if st.session_state.get("selected_batch") in ids else 0,
        format_func=lambda batch_id: f"{by_id[batch_id].get('name') or short_id(batch_id)} · {batch_state_text(by_id[batch_id].get('status'))} · {batch_id}",
    )
    st.session_state.selected_batch = selected
    if refresh.button("刷新进度", icon=":material/refresh:", width="stretch"):
        st.rerun()
    batch = call("GET", f"/v1/batches/{selected}")
    if not batch:
        return
    total = batch.get("total") or 0
    done = (batch.get("succeeded") or 0) + (batch.get("failed") or 0)
    with st.container(border=True):
        st.markdown(f"**{batch.get('name') or '批次 ' + short_id(selected)}** " + batch_badge(batch.get("status")))
        st.progress(done / total if total else 0, text=f"总数 {total} · 成功 {batch.get('succeeded', 0)} · 失败 {batch.get('failed', 0)} · 待处理 {batch.get('pending', 0)}")
        errors = call("GET", f"/v1/batches/{selected}/errors")
        result_csv = call("GET", f"/v1/batches/{selected}/download")
        downloads = st.columns(2)
        if isinstance(errors, bytes):
            downloads[0].download_button("下载错误清单 CSV", errors, file_name=f"{selected}-errors.csv", mime="text/csv", icon=":material/error:", width="stretch")
        if isinstance(result_csv, bytes):
            downloads[1].download_button("下载完整结果 CSV", result_csv, file_name=f"{selected}-results.csv", mime="text/csv", icon=":material/download:", width="stretch")
    if isinstance(errors, bytes):
        error_rows = list(csv.DictReader(io.StringIO(errors.decode("utf-8-sig"))))
        st.subheader("错误行")
        as_table(error_rows, columns=["row_number", "external_id", "error_code", "error_reason"], empty="此批次没有错误行。")
    if isinstance(result_csv, bytes):
        try:
            preview = list(csv.DictReader(io.StringIO(result_csv.decode("utf-8-sig"))))
        except UnicodeError:
            st.info("请下载结果查看。")
            return
        pending_reviews = [item for item in preview if item.get("review_status") == "pending_review"]
        if pending_reviews:
            st.subheader("需人工复核")
            st.warning(f"此批次有 {len(pending_reviews)} 条记录需要人工复核。复核原因已列在结果中；你可以在“待复核工单”页面处理自己的或其他用户的记录。")
            as_table(pending_reviews, columns=["row_number", "external_id", "labels", "review_reasons"])
            st.button("打开待复核工单", key=f"open_review_queue_{selected}", on_click=navigate_to, args=("待复核工单",), icon=":material/fact_check:")
        with st.expander("预览结果（包括行级模型和降级状态）"):
            as_table(preview[:100])


def history_page() -> None:
    page_header("我的提交与复核历史", "查看自己提交的工单及其复核结果，以及自己处理过的复核。")
    submissions = rows(call("GET", "/v1/classifications"))
    reviews = rows(call("GET", "/v1/reviews/history"))
    metric_row([
        ("提交工单", len(submissions)),
        ("待复核", sum(1 for item in submissions if item.get("review_status") == "pending_review")),
        ("已复核", sum(1 for item in submissions if item.get("review_status") in ("confirmed", "corrected"))),
        ("我处理的复核", len(reviews)),
    ])
    submitted_tab, reviewed_tab = st.tabs(["提交历史", "我处理的复核"])
    with submitted_tab:
        as_table(submissions, columns=["ticket_id", "external_id", "final_labels", "review_status", "review_reasons", "model_version", "degraded"], empty="还没有提交过工单。")
    with reviewed_tab:
        as_table(reviews, columns=["ticket_id", "decision", "original_labels", "final_labels", "comment", "created_at"], empty="还没有处理过复核。")


def bulk_delete_controls(title: str, path: str, items: list[dict[str, Any]], id_key: str, *, name_key: str | None = None) -> None:
    id_to_label = {
        str(item[id_key]): f"{item.get(name_key)} · {item[id_key]}" if name_key and item.get(name_key) else str(item[id_key])
        for item in items if item.get(id_key)
    }
    selected = st.multiselect(f"选择要批量删除的{title}", list(id_to_label), format_func=lambda item_id: id_to_label[item_id], key=f"bulk_select_{path}")
    if not selected:
        return
    with st.form(f"bulk_delete_{path}"):
        confirm = st.checkbox(f"确认物理删除所选 {len(selected)} 条{title}；已有历史与审计保留")
        submitted = st.form_submit_button(f"批量删除所选{title}", icon=":material/delete:")
    if submitted:
        if not confirm:
            st.warning("请先确认批量删除。")
        elif call("DELETE", path, json={"ids": selected}) is not None:
            st.rerun()


def is_high_risk(item: dict[str, Any]) -> bool:
    reasons = item.get("review_reasons") or []
    reasons = reasons.split("|") if isinstance(reasons, str) else reasons
    labels = item.get("labels") or item.get("original_labels") or []
    return "supplier_master_change" in reasons or bool(HIGH_RISK_LABELS & set(labels))


def review_page() -> None:
    page_header("待复核工单", "逐条核对模型建议：保持标签即确认，调整标签即修正。供应商主数据变更属于高风险，请优先处理。")
    queue = rows(call("GET", "/v1/reviews/queue"))
    metric_row([
        ("待复核", len(queue)),
        ("高风险", sum(1 for item in queue if is_high_risk(item))),
        ("低置信度", sum(1 for item in queue if "low_confidence" in (item.get("review_reasons") or []))),
        ("备用模型", sum(1 for item in queue if item.get("degraded"))),
    ])
    if not queue:
        st.success("当前没有待复核工单。")
        return
    for item in queue:
        ticket_id = str(item.get("ticket_id") or item.get("id"))
        version = item.get("state_version", item.get("version"))
        labels = item.get("labels") or item.get("original_labels") or []
        risk = is_high_risk(item)
        title = f"{'高风险 · ' if risk else ''}工单 {short_id(ticket_id)} · 原预测：{labels_text(labels)}"
        with st.expander(title, icon=":material/priority_high:" if risk else ":material/rule:"):
            text_col, form_col = st.columns([1.2, 1], gap="large")
            with text_col:
                st.markdown("**脱敏文本**")
                with st.container(border=True):
                    st.text(item.get("redacted_text") or item.get("text") or "—")
                st.markdown("**原预测** " + label_badges(labels))
                st.markdown("**复核原因** " + review_reasons_text(item.get("review_reasons")))
                model_notice(item)
                st.caption(f"工单 ID：{ticket_id} · 外部编号：{item.get('external_id') or '—'} · 状态版本：{version}")
            with form_col:
                with st.form(f"review_{ticket_id}"):
                    proposed = st.multiselect("最终标签", LABELS, default=[label for label in labels if label in LABELS], format_func=label_name)
                    comment = st.text_area("意见（可选）")
                    submitted = st.form_submit_button("提交复核", type="primary", icon=":material/check:")
                if submitted:
                    if version is None:
                        st.error("缺少状态版本，请刷新队列。")
                        continue
                    original = set(labels)
                    decision = "confirmed" if set(proposed) == original else "corrected"
                    result = call("POST", f"/v1/reviews/{ticket_id}", json={"expected_version": version, "decision": decision, "final_labels": proposed, "comment": comment})
                    if result is not None:
                        st.success("复核已提交。")
                        st.rerun()


def admin_users(users: list[dict[str, Any]]) -> None:
    st.subheader("用户与角色")
    as_table(users, columns=["email", "role", "active", "id"])
    with st.expander("新增用户", icon=":material/person_add:"):
        with st.form("add_user"):
            email = st.text_input("邮箱")
            password = st.text_input("初始密码", type="password", help=PASSWORD_HINT)
            st.caption(PASSWORD_HINT)
            role = st.selectbox("角色", ["user", "admin"], format_func=lambda value: ROLES[value])
            submitted = st.form_submit_button("创建")
        problem = password_problem(password) if submitted else None
        if problem:
            st.error(problem)
        elif submitted and call("POST", "/v1/admin/users", json={"email": email.strip(), "password": password, "role": role}) is not None:
            st.rerun()
    if not users:
        return
    chosen = st.selectbox("选择用户", users, format_func=lambda user: f"{user.get('email')} ({user.get('id')})")
    edit_col, delete_col = st.columns(2, gap="large")
    with edit_col:
        with st.form("edit_user"):
            new_role = st.selectbox("角色", ["user", "admin"], index=1 if chosen.get("role") == "admin" else 0, format_func=lambda value: ROLES[value])
            active = st.checkbox("启用账号", value=bool(chosen.get("active", True)))
            submitted = st.form_submit_button("保存用户")
        if submitted and call("PATCH", f"/v1/admin/users/{chosen['id']}", json={"role": new_role, "active": active}) is not None:
            st.rerun()
    with delete_col:
        with st.form("delete_user"):
            confirm = st.checkbox("确认删除此账号；审计仍保留稳定操作者标识")
            submitted = st.form_submit_button("删除用户", icon=":material/delete:")
        if submitted and confirm and call("DELETE", f"/v1/admin/users/{chosen['id']}") is not None:
            st.rerun()


TICKET_COLUMNS = ["ticket_id", "external_id", "final_labels", "review_status", "review_reasons", "model_version", "degraded", "content_version", "submitted_by"]


def admin_business(tickets: list[dict[str, Any]]) -> None:
    st.subheader("业务工单与分类记录")
    with st.expander("新增工单", icon=":material/note_add:"):
        with st.form("add_ticket"):
            text = st.text_area("工单文本")
            submitted = st.form_submit_button("创建工单")
        if submitted and call("POST", "/v1/admin/tickets", json={"text": text.strip()}) is not None:
            st.rerun()
    as_table(tickets, columns=TICKET_COLUMNS)
    bulk_delete_controls("工单", "/v1/admin/tickets", tickets, "ticket_id")
    if tickets:
        chosen = st.selectbox("选择工单", tickets, format_func=lambda item: str(item.get("ticket_id")))
        ticket_id = chosen.get("ticket_id")
        st.markdown("**当前状态** " + review_badge(chosen.get("review_status")) + " " + label_badges(chosen.get("final_labels") or chosen.get("labels")))
        if chosen.get("review_status") == "pending_reclassification":
            st.info("该工单待重新分类（新增/修改时模型不可用，或分类记录已被删除）。")
            if st.button("重新分类", key=f"reclassify_{ticket_id}", icon=":material/autorenew:") and call("POST", f"/v1/admin/tickets/{ticket_id}/reclassify") is not None:
                st.rerun()
        edit_col, delete_col = st.columns([1.6, 1], gap="large")
        with edit_col:
            with st.form("edit_ticket"):
                new_text = st.text_area("修改文本（保存后产生新版本并立即重新分类）", value=chosen.get("redacted_text") or "")
                submitted = st.form_submit_button("保存新版本")
            if submitted and call("PATCH", f"/v1/admin/tickets/{ticket_id}", json={"text": new_text.strip()}) is not None:
                st.rerun()
        with delete_col:
            with st.form("delete_ticket"):
                confirm = st.checkbox("确认物理删除活动工单；独立历史与删除审计保留")
                submitted = st.form_submit_button("删除工单", icon=":material/delete:")
            if submitted and confirm and call("DELETE", f"/v1/admin/tickets/{ticket_id}") is not None:
                st.rerun()
    st.subheader("分类记录")
    predictions = [ticket for ticket in tickets if ticket.get("prediction_id")]
    if not predictions:
        st.caption("当前没有活动分类记录。")
        return
    as_table(predictions, columns=["prediction_id", "ticket_id", "labels", "final_labels", "review_status", "model_version", "threshold_version", "degraded"])
    bulk_delete_controls("分类记录", "/v1/admin/predictions", predictions, "prediction_id")
    selected = st.selectbox("选择分类记录", predictions, format_func=lambda item: str(item.get("prediction_id")))
    edit_col, delete_col = st.columns([1.6, 1], gap="large")
    with edit_col:
        with st.form("edit_prediction"):
            labels = st.multiselect("人工修订标签", LABELS, default=[label for label in selected.get("labels", []) if label in LABELS], format_func=label_name)
            comment = st.text_area("修订意见")
            submitted = st.form_submit_button("追加人工决定")
        if submitted and call("PATCH", f"/v1/admin/predictions/{selected['prediction_id']}", json={"final_labels": labels, "comment": comment}) is not None:
            st.rerun()
    with delete_col:
        with st.form("delete_prediction"):
            confirm = st.checkbox("确认物理删除活动分类记录；原预测与决定留在独立历史")
            submitted = st.form_submit_button("删除分类记录", icon=":material/delete:")
        if submitted and confirm and call("DELETE", f"/v1/admin/predictions/{selected['prediction_id']}") is not None:
            st.rerun()


def admin_batches(batches: list[dict[str, Any]]) -> None:
    st.subheader("批次管理")
    as_table(batches, columns=["name", "status", "total", "succeeded", "failed", "pending", "id"])
    bulk_delete_controls("批次", "/v1/admin/batches", batches, "id", name_key="name")
    if not batches:
        return
    chosen = st.selectbox("选择批次", batches, format_func=lambda item: str(item.get("id")))
    edit_col, delete_col = st.columns([1.6, 1], gap="large")
    with edit_col:
        with st.form("edit_batch"):
            name = st.text_input("批次名称", value=chosen.get("name") or "")
            submitted = st.form_submit_button("保存元数据")
        if submitted and call("PATCH", f"/v1/admin/batches/{chosen['id']}", json={"name": name}) is not None:
            st.rerun()
    with delete_col:
        with st.form("delete_batch"):
            confirm = st.checkbox("确认物理删除活动批次；独立历史与删除审计保留")
            submitted = st.form_submit_button("删除批次", icon=":material/delete:")
        if submitted and confirm and call("DELETE", f"/v1/admin/batches/{chosen['id']}") is not None:
            st.rerun()


def admin_page() -> None:
    page_header("管理员", "维护用户与角色、业务工单、分类记录和批次；所有删除均保留独立历史与审计。")
    users = rows(call("GET", "/v1/admin/users"))
    tickets = rows(call("GET", "/v1/admin/tickets"))
    batches = rows(call("GET", "/v1/admin/batches"))
    metric_row([
        ("启用用户", f"{sum(1 for user in users if user.get('active', True))} / {len(users)}"),
        ("工单总数", len(tickets)),
        ("待复核", sum(1 for ticket in tickets if ticket.get("review_status") == "pending_review")),
        ("待重新分类", sum(1 for ticket in tickets if ticket.get("review_status") == "pending_reclassification")),
        ("批次", len(batches)),
    ])
    tabs = st.tabs(["用户", "工单与分类", "批次", "独立历史"])
    with tabs[0]:
        admin_users(users)
    with tabs[1]:
        admin_business(tickets)
    with tabs[2]:
        admin_batches(batches)
    with tabs[3]:
        st.caption("活动记录删除后，预测、人工决定和删除审计仍可追溯。")
        history = call("GET", "/v1/admin/history")
        if isinstance(history, dict):
            st.subheader("原预测与历史版本")
            as_table(rows(history.get("prediction_history")), columns=["created_at", "ticket_id", "ticket_version", "labels", "review_reason", "model_version", "degraded", "id"])
            st.subheader("人工复核决定")
            as_table(rows(history.get("review_decisions")), columns=["created_at", "ticket_id", "decision", "original_labels", "final_labels", "comment", "reviewer_id"])
            st.subheader("已删除批次行")
            as_table(rows(history.get("batch_item_history")), columns=["archived_at", "batch_id", "row_number", "external_id", "status", "error_code", "error_reason", "deleted_by"])
            st.subheader("审计事件")
            as_table(rows(history.get("audit_events")), columns=["created_at", "action", "object_type", "object_id", "actor_id", "details"])


def main() -> None:
    restore_session()
    sync_auth_cookie()
    if not st.session_state.get("token"):
        sign_in()
        return
    user = call("GET", "/v1/auth/me")
    if not user:
        return
    st.session_state.user = user
    inject_css()
    state, readiness = readiness_status()
    page = sidebar(user, state, readiness)
    if page == "单条分类":
        classify_page(state, readiness)
    elif page == "CSV 批量":
        batch_page()
    elif page == "我的历史":
        history_page()
    elif page == "待复核工单":
        review_page()
    elif user.get("role") == "admin":
        admin_page()


if __name__ == "__main__":
    main()
