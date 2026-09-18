import type { AdminTicket, AuditEvent, BatchStatus, ClassificationResponse, ReviewItem, TaxonomyResponse } from "./types";

const now = new Date().toISOString();

export const mockTaxonomy: TaxonomyResponse = {
  taxonomy_version: "invoiceops-v1",
  routing_version: "routes-v1",
  labels: [
    { code: "PRICE_VARIANCE", name: "价格差异", risk: "medium" },
    { code: "QUANTITY_RECEIPT_VARIANCE", name: "数量/收货差异", risk: "medium" },
    { code: "TAX_CURRENCY_AMOUNT", name: "税务/币种/金额异常", risk: "high" },
    { code: "DUPLICATE_INVOICE", name: "疑似重复发票", risk: "medium" },
    { code: "MISSING_PO_OR_RECEIPT", name: "缺少 PO/收货凭证", risk: "medium" },
    { code: "PAYMENT_STATUS", name: "付款/发票状态咨询", risk: "low" },
    { code: "SUPPLIER_MASTER_CHANGE", name: "供应商主数据变更", risk: "high" },
    { code: "OTHER_REVIEW", name: "其他人工复核", risk: "medium" },
  ],
};

export const mockClassification: ClassificationResponse = {
  request_id: "demo-request-001",
  decision: "needs_review",
  reason_codes: ["LOW_CONFIDENCE", "HIGH_RISK_LABEL"],
  language: "mixed",
  predictions: [
    { label_code: "TAX_CURRENCY_AMOUNT", score: 0.91 },
    { label_code: "PAYMENT_STATUS", score: 0.88 },
  ],
  route: { primary: "FINANCE_TAX", collaborators: ["SUPPLIER_HELPDESK"], version: "routes-v1" },
  model_version: "invoiceops-xlmr-demo",
  threshold_version: "thresholds-v1",
  taxonomy_version: "invoiceops-v1",
  inference_ms: 42,
  trace_id: "demo-trace-001",
  mock: true,
};

export const mockReviews: ReviewItem[] = [
  {
    ticket_id: "demo-ticket-001",
    request_id: "demo-request-001",
    risk: "high",
    decision: "needs_review",
    primary_queue: "FINANCE_TAX",
    collaborator_queues: ["SUPPLIER_HELPDESK"],
    predictions: mockClassification.predictions,
    reason_codes: ["HIGH_RISK_LABEL", "LOW_CONFIDENCE"],
    sanitized_text: "The invoice amount is wrong，而且我们还没有收到付款。",
    waiting_seconds: 420,
    overdue: false,
    review_sla_minutes: 60,
  },
  {
    ticket_id: "demo-ticket-002",
    request_id: "demo-request-002",
    risk: "medium",
    decision: "needs_review",
    primary_queue: "PROCUREMENT_OPERATIONS",
    collaborator_queues: [],
    predictions: [{ label_code: "PRICE_VARIANCE", score: 0.62 }],
    reason_codes: ["LOW_CONFIDENCE"],
    sanitized_text: "The PO price does not match the invoice price.",
    waiting_seconds: 180,
    overdue: false,
    review_sla_minutes: 60,
  },
];

export const mockAdminTickets: AdminTicket[] = [
  {
    ticket_id: "demo-admin-ticket-001",
    request_id: "demo-request-001",
    source: "email",
    sanitized_text: "发票金额不正确，而且我们还没有收到付款。",
    language: "mixed",
    risk: "high",
    status: "needs_review",
    labels: ["TAX_CURRENCY_AMOUNT", "PAYMENT_STATUS"],
    primary_queue: "FINANCE_TAX",
    created_at: now,
  },
  {
    ticket_id: "demo-admin-ticket-002",
    request_id: "demo-request-002",
    source: "portal",
    sanitized_text: "采购订单价格与发票价格不一致。",
    language: "zh",
    risk: "medium",
    status: "classified",
    labels: ["PRICE_VARIANCE"],
    primary_queue: "PROCUREMENT_OPERATIONS",
    created_at: now,
  },
];

export const mockBatch: BatchStatus = {
  batch_id: "demo-batch-001",
  status: "completed",
  total_rows: 3,
  processed_rows: 3,
  success_rows: 2,
  failed_rows: 1,
  row_errors: [{ row_number: 3, code: "ValidationError", message: "request_id and text are required" }],
  mock: true,
};

export const mockAudit: AuditEvent[] = [
  { event_id: "event-1", event_type: "ticket.received", event_version: "1", occurred_at: now, request_id: "demo-request-001", trace_id: "demo-trace-001", actor: { id: "api" }, subject: { id: "demo-ticket-001" }, payload: { source: "email" } },
  { event_id: "event-2", event_type: "prediction.created", event_version: "1", occurred_at: now, request_id: "demo-request-001", trace_id: "demo-trace-001", actor: { id: "classifier" }, subject: { id: "demo-prediction-001" }, payload: { decision: "needs_review" } },
  { event_id: "event-3", event_type: "route.recommended", event_version: "1", occurred_at: now, request_id: "demo-request-001", trace_id: "demo-trace-001", actor: { id: "router" }, subject: { id: "demo-ticket-001" }, payload: { primary: "FINANCE_TAX", version: "routes-v1" } },
];

export const mockMetricsText = `invoiceops_tickets_total 24
invoiceops_audit_events_total 96
invoiceops_error_rate 0.042
invoiceops_latency_p95_ms 410
invoiceops_review_backlog 7
invoiceops_llm_fallback_total 3
`;
