const LABEL_NAMES: Record<string, string> = {
  PRICE_VARIANCE: "价格差异",
  QUANTITY_RECEIPT_VARIANCE: "数量/收货差异",
  TAX_CURRENCY_AMOUNT: "税务/币种/金额异常",
  DUPLICATE_INVOICE: "疑似重复发票",
  MISSING_PO_OR_RECEIPT: "缺少采购或收货凭证",
  PAYMENT_STATUS: "付款状态咨询",
  SUPPLIER_MASTER_CHANGE: "供应商主数据/收款信息变更",
  OTHER_REVIEW: "其他人工复核",
};

const ROUTE_NAMES: Record<string, string> = {
  PROCUREMENT_OPERATIONS: "采购运营",
  RECEIPT_PROCUREMENT: "收货采购",
  FINANCE_TAX: "财务税务",
  AP_REVIEW: "应付账款复核",
  SUPPLIER_HELPDESK: "供应商服务台",
  MASTER_DATA_RISK: "主数据风险",
  MANUAL_TRIAGE: "人工分流",
};

export const routeCodes = Object.keys(ROUTE_NAMES);

const REASON_NAMES: Record<string, string> = {
  LOW_CONFIDENCE: "置信度较低",
  HIGH_RISK_LABEL: "包含高风险标签",
  NO_CANDIDATE_LABEL: "未识别到有效标签",
  CONFLICTING_LABELS: "标签组合存在冲突",
};

const SOURCE_NAMES: Record<string, string> = {
  email: "邮件",
  portal: "供应商门户",
  api: "接口",
  manual: "人工录入",
};

const RISK_NAMES: Record<string, string> = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
};

const STATUS_NAMES: Record<string, string> = {
  received: "已接入",
  classified: "已分类",
  needs_review: "待人工复核",
  reviewed: "已复核",
  queued: "排队中",
  processing: "处理中",
  completed: "已完成",
  failed: "处理失败",
  cancelled: "已取消",
};

const METRIC_NAMES: Record<string, string> = {
  invoiceops_tickets_total: "工单总数",
  invoiceops_audit_events_total: "审计事件总数",
  invoiceops_error_rate: "错误率",
  invoiceops_latency_p95_ms: "接口延迟 P95（毫秒）",
  invoiceops_review_backlog: "待复核积压量",
  invoiceops_llm_fallback_total: "智能辅助降级次数",
};

const EVENT_NAMES: Record<string, string> = {
  "ticket.received": "工单已接入",
  "prediction.created": "分类预测已生成",
  "route.recommended": "路由建议已生成",
  "review.appended": "人工复核已追加",
  "review.sla_breached": "复核时限已超期",
  "llm.suggestion.created": "智能辅助建议已生成",
  "classification.completed": "分类处理已完成",
  "authorization.denied": "授权被拒绝",
  "ticket.deleted": "工单已删除",
  "ticket.overwritten": "工单已覆盖",
  "tickets.deleted": "批量删除已完成",
  "tickets.batch_overwritten": "批量覆盖已完成",
};

const ERROR_MESSAGES: Record<string, string> = {
  "Bearer token required": "请先登录后再进行此操作。",
  "invalid token": "登录状态已失效，请重新登录。",
  "insufficient role": "当前账号没有执行此操作的权限。",
  "request validation failed": "请求参数校验失败，请检查输入内容。",
  "request_id already exists": "请求编号已存在，请更换请求编号后重试。",
  "ticket not found": "工单不存在。",
  "one or more tickets not found": "一个或多个工单不存在。",
  "overwrite request_id values must be unique": "覆盖数据中的请求编号不能重复。",
  "当前账号没有执行此操作的权限。": "当前账号没有执行此操作的权限。",
  "idempotency key was already used with a different request": "幂等键已用于其他请求，请更换幂等键后重试。",
  "batch file must be CSV": "批次文件必须是 CSV 格式。",
  "request_id and text are required": "请求编号和工单正文不能为空。",
  "未配置 VITE_API_BASE_URL，当前为本地演示模式。": "未配置后端接口地址，当前为本地演示模式。",
};

export function labelName(code: string): string {
  return LABEL_NAMES[code] ?? "未识别标签";
}

export function routeName(code: string): string {
  return ROUTE_NAMES[code] ?? "未分配责任队列";
}

export function reasonName(code: string): string {
  return REASON_NAMES[code] ?? "其他复核原因";
}

export function sourceName(source: string): string {
  return SOURCE_NAMES[source] ?? "其他来源";
}

export function riskName(risk: string): string {
  return RISK_NAMES[risk] ?? "未知风险";
}

export function statusName(status: string): string {
  return STATUS_NAMES[status] ?? "未知状态";
}

export function metricName(name: string): string {
  return METRIC_NAMES[name] ?? "其他运行指标";
}

export function eventName(eventType: string): string {
  return EVENT_NAMES[eventType] ?? "其他审计事件";
}

export function languageName(language: string): string {
  return { zh: "中文", en: "英文", mixed: "中英混合", unknown: "未知" }[language] ?? "未知";
}

export function roleName(role: string): string {
  return { admin: "管理员", reviewer: "复核员", observer: "观察者" }[role] ?? "未知角色";
}

export function modeName(mode: string): string {
  return mode === "api" ? "真实接口" : "本地演示";
}

export function decisionName(decision: string): string {
  return decision === "needs_review" ? "需要人工复核" : decision === "classified" ? "自动分类" : "未知结果";
}

export function rowErrorCode(code: string): string {
  return { ValueError: "数据格式错误", ValidationError: "数据校验失败" }[code] ?? "处理错误";
}

export function rowErrorMessage(message: string): string {
  if (message.startsWith("CSV must contain")) return "CSV 文件必须包含：请求编号、来源、正文、标签体系版本。";
  if (message.startsWith("request_id and text are required")) return "请求编号和工单正文不能为空。";
  if (message.startsWith("batch file must be CSV")) return "批次文件必须是 CSV 格式。";
  return /[A-Za-z]/.test(message) ? "该行数据处理失败，请检查格式和必填字段。" : message;
}

export function errorMessage(cause: unknown, fallback: string): string {
  if (!(cause instanceof Error)) return fallback;
  return ERROR_MESSAGES[cause.message] ?? (/[A-Za-z]/.test(cause.message) ? fallback : cause.message);
}
