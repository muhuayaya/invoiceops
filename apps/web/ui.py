"""Presentation helpers for the InvoiceOps operations console (display only, no API calls)."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import streamlit as st


# 标签代码保持 API 原值；这里只提供界面显示名。
LABEL_NAMES = {
    "DUPLICATE_INVOICE": "重复发票",
    "MISSING_PO_OR_RECEIPT": "缺采购单/收货单",
    "OTHER_REVIEW": "其他需复核",
    "PAYMENT_STATUS": "付款状态查询",
    "PRICE_VARIANCE": "价格差异",
    "QUANTITY_RECEIPT_VARIANCE": "数量/收货差异",
    "SUPPLIER_MASTER_CHANGE": "供应商主数据变更",
    "TAX_CURRENCY_AMOUNT": "税额/币种/金额",
}
HIGH_RISK_LABELS = {"SUPPLIER_MASTER_CHANGE"}

REVIEW_STATUS = {
    "not_required": ("无需复核", "green"),
    "pending_review": ("待复核", "orange"),
    "pending_reclassification": ("待重新分类", "violet"),
    "confirmed": ("已确认", "blue"),
    "corrected": ("已修正", "blue"),
}
BATCH_STATUS = {
    "pending": ("排队中", "gray"),
    "running": ("处理中", "blue"),
    "paused": ("已暂停", "orange"),
    "completed": ("已完成", "green"),
}
ROW_STATUS = {"pending": "待处理", "succeeded": "成功", "failed": "失败"}
DECISIONS = {"confirmed": "确认原预测", "corrected": "修正标签"}
ROLES = {"admin": "管理员", "user": "普通用户"}
REVIEW_REASONS = {
    "supplier_master_change": "高风险：供应商主数据变更",
    "low_confidence": "低置信度：预测概率接近分类阈值",
    "no_label": "未命中分类",
    "model_fallback": "使用备用模型分类",
}
AUDIT_ACTIONS = {
    "ticket.create": "新增工单",
    "ticket.revise": "修改工单",
    "ticket.delete": "删除工单",
    "ticket.reclassify": "重新分类",
    "prediction.revise": "修订分类",
    "prediction.delete": "删除分类记录",
    "batch.delete": "删除批次",
    "batch.rename": "批次改名",
    "user.create": "新增用户",
    "user.update": "修改用户",
    "user.delete": "删除用户",
    "review.submit": "提交复核",
}

COLUMN_NAMES = {
    "id": "ID",
    "ticket_id": "工单号",
    "prediction_id": "分类记录",
    "batch_id": "批次",
    "external_id": "外部编号",
    "name": "批次名称",
    "email": "邮箱",
    "role": "角色",
    "active": "账号状态",
    "status": "状态",
    "row_status": "行状态",
    "total": "总行数",
    "succeeded": "成功",
    "failed": "失败",
    "pending": "待处理",
    "labels": "模型标签",
    "final_labels": "最终标签",
    "original_labels": "原预测标签",
    "review_status": "复核状态",
    "review_reasons": "复核原因",
    "review_reason": "复核原因",
    "decision": "复核决定",
    "comment": "意见",
    "model_version": "模型版本",
    "actual_model_version": "实际模型",
    "threshold_version": "阈值版本",
    "taxonomy_version": "标签版本",
    "degraded": "备用模型",
    "fallback_reason": "降级原因",
    "state_version": "状态版本",
    "content_version": "内容版本",
    "ticket_version": "工单版本",
    "version": "版本",
    "redacted_text": "脱敏文本",
    "submitted_by": "提交人",
    "reviewer_id": "复核人",
    "actor_id": "操作人",
    "deleted_by": "删除人",
    "action": "操作",
    "object_type": "对象类型",
    "object_id": "对象 ID",
    "details": "详情",
    "row_number": "行号",
    "error_code": "错误代码",
    "error_reason": "错误说明",
    "probabilities": "各类概率",
    "created_at": "时间",
    "archived_at": "归档时间",
}
LABEL_FIELDS = {"labels", "final_labels", "original_labels"}
REASON_FIELDS = {"review_reasons", "review_reason"}
LOCAL_TZ = timezone(timedelta(hours=8), "UTC+8")


PASSWORD_HINT = "密码须为 12–64 位英文字母与数字的组合，至少包含一个字母和一个数字，不得含空格或特殊字符。"
_PASSWORD_PATTERN = re.compile(r"(?=.*[A-Za-z])(?=.*[0-9])[A-Za-z0-9]{12,64}")


def password_problem(password: str) -> str | None:
    """Mirror of the server rule for new passwords; returns a Chinese message or None."""
    if not password:
        return "请输入密码。"
    if not 12 <= len(password) <= 64:
        return f"密码长度须为 12–64 位（当前 {len(password)} 位）。"
    if not re.fullmatch(r"[A-Za-z0-9]+", password):
        return "密码只能包含英文字母和数字，不得含空格、符号或中文等其他字符。"
    if not _PASSWORD_PATTERN.fullmatch(password):
        return "密码须同时包含英文字母和数字。"
    return None


def label_name(code: str) -> str:
    return LABEL_NAMES.get(code, code)


def labels_text(labels: Any) -> str:
    values = labels.split("|") if isinstance(labels, str) else labels or []
    return "、".join(label_name(str(label)) for label in values if label) or "—"


def review_reasons_text(reasons: Any) -> str:
    values = reasons.split("|") if isinstance(reasons, str) else reasons or []
    return "、".join(REVIEW_REASONS.get(str(reason), str(reason)) for reason in values if reason) or "—"


def review_state_text(status: str | None) -> str:
    return REVIEW_STATUS.get(status or "", (status or "—", "gray"))[0]


def batch_state_text(status: str | None) -> str:
    return BATCH_STATUS.get(status or "", (status or "—", "gray"))[0]


def badge(text: str, color: str = "gray") -> str:
    """Streamlit markdown badge; text is escaped for the badge directive."""
    safe = str(text).replace("[", "(").replace("]", ")")
    return f":{color}-badge[{safe}]"


def review_badge(status: str | None) -> str:
    text, color = REVIEW_STATUS.get(status or "", (status or "—", "gray"))
    return badge(text, color)


def batch_badge(status: str | None) -> str:
    text, color = BATCH_STATUS.get(status or "", (status or "—", "gray"))
    return badge(text, color)


def label_badges(labels: Any) -> str:
    values = labels.split("|") if isinstance(labels, str) else labels or []
    return " ".join(badge(label_name(str(label)), "red" if label in HIGH_RISK_LABELS else "blue") for label in values if label) or badge("未命中分类", "gray")


def short_id(value: Any) -> str:
    text = str(value or "—")
    return text[:8] if len(text) > 12 else text


def format_time(value: Any) -> str:
    if not value:
        return "—"
    try:
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


def display_value(key: str, value: Any) -> Any:
    if value is None or value == "":
        return "—"
    if key in LABEL_FIELDS:
        return labels_text(value)
    if key in REASON_FIELDS:
        return review_reasons_text(value)
    if key == "review_status":
        return review_state_text(value)
    if key == "status" and value in BATCH_STATUS:
        return batch_state_text(value)
    if key in ("status", "row_status") and value in ROW_STATUS:
        return ROW_STATUS[value]
    if key == "decision":
        return DECISIONS.get(value, value)
    if key == "role":
        return ROLES.get(value, value)
    if key == "active":
        return "启用" if value else "停用"
    if key == "degraded":
        return "是" if str(value).lower() in ("true", "1") else "否"
    if key == "action":
        return AUDIT_ACTIONS.get(value, value)
    if key in ("created_at", "archived_at"):
        return format_time(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def display_rows(data: list[dict[str, Any]], columns: list[str] | None = None) -> list[dict[str, Any]]:
    keys = columns or list(dict.fromkeys(key for row in data for key in row))
    return [{COLUMN_NAMES.get(key, key): display_value(key, row.get(key)) for key in keys} for row in data]


CONSOLE_CSS = """
<style>
.block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1400px;}
h1, h2, h3 {letter-spacing: 0.01em;}
[data-testid="stMain"] h2 {border-left: 4px solid #1F5EFF; padding-left: 0.65rem; margin-bottom: 0.1rem;}
[data-testid="stMetric"] {background: #FFFFFF; box-shadow: 0 1px 2px rgba(20, 33, 61, 0.06);}
[data-testid="stMetricLabel"] p {color: #5B6B86; font-size: 0.82rem;}
[data-testid="stExpander"] details {background: #FFFFFF;}
[data-testid="stForm"] {background: #FFFFFF;}
[data-testid="stVerticalBlockBorderWrapper"] > div > [data-testid="stVerticalBlock"] {gap: 0.7rem;}
.io-brand {display: flex; align-items: center; gap: 0.6rem; padding: 0.2rem 0 0.9rem 0;}
.io-brand-mark {width: 2.1rem; height: 2.1rem; border-radius: 0.5rem; background: linear-gradient(135deg, #1F5EFF, #38BDF8);
  color: #FFFFFF; font-weight: 700; display: flex; align-items: center; justify-content: center; font-size: 1rem;}
.io-brand-name {color: #FFFFFF; font-weight: 700; font-size: 1.08rem; line-height: 1.2;}
.io-brand-sub {color: #8FA3C4; font-size: 0.74rem; line-height: 1.2;}
.io-section {color: #7D90B0; font-size: 0.72rem; letter-spacing: 0.08em; margin: 0.6rem 0 0.2rem 0;}
[data-testid="stSidebar"] [role="radiogroup"] {gap: 0.15rem;}
[data-testid="stSidebar"] [data-testid="stRadio"], [data-testid="stSidebar"] [data-testid="stRadio"] > div, [data-testid="stSidebar"] [role="radiogroup"], [data-testid="stSidebar"] [role="radiogroup"] > div {width: 100%;}
[data-testid="stSidebar"] [data-testid="stRadioOption"] {width: 100%; box-sizing: border-box; padding: 0.5rem 0.75rem; border-radius: 0.45rem; margin: 0;}
[data-testid="stSidebar"] [role="radiogroup"] label:hover {background: #17294A;}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background: #1F5EFF;}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p {color: #FFFFFF; font-weight: 600;}
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div:not([data-testid="stMarkdownContainer"]) {display: none;}
.io-user {color: #FFFFFF; font-weight: 600; font-size: 0.9rem; word-break: break-all;}
.io-login-hero {padding: 2.4rem 0 1.2rem 0;}
.io-login-hero .io-brand-name {color: #14213D; font-size: 1.6rem;}
.io-login-hero .io-brand-sub {color: #5B6B86; font-size: 0.9rem;}
.io-login-hero .io-brand-mark {width: 2.8rem; height: 2.8rem; font-size: 1.3rem;}
.io-feature {color: #3A4A66; font-size: 0.9rem; margin: 0.35rem 0;}
</style>
"""


def inject_css() -> None:
    st.html(CONSOLE_CSS)


def brand_html(subtitle: str = "发票工单运营台") -> str:
    return (
        '<div class="io-brand"><div class="io-brand-mark">IO</div>'
        f'<div><div class="io-brand-name">InvoiceOps</div><div class="io-brand-sub">{subtitle}</div></div></div>'
    )


def user_html(email: Any) -> str:
    return f'<div class="io-user">{html.escape(str(email or "—"))}</div>'


def page_header(title: str, caption: str) -> None:
    st.header(title)
    st.caption(caption)
