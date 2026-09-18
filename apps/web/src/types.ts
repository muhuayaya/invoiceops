export type Role = "admin" | "reviewer" | "observer";
export type AppMode = "api" | "local-demo";
export type TicketSource = "email" | "portal" | "api" | "manual";
export type TicketRisk = "low" | "medium" | "high";
export type TicketStatus = "received" | "classified" | "needs_review" | "reviewed";

export interface Session {
  accessToken: string;
  username: string;
  role: Role;
  mode: AppMode;
}

export interface LabelScore {
  label_code: string;
  score: number;
}

export interface ClassificationResponse {
  request_id: string;
  decision: "classified" | "needs_review";
  reason_codes?: string[];
  language: "zh" | "en" | "mixed" | "unknown";
  predictions: LabelScore[];
  route: { primary: string; collaborators: string[]; version: string };
  model_version: string;
  threshold_version: string;
  taxonomy_version: string;
  inference_ms?: number;
  trace_id: string;
  mock?: boolean;
}

export interface AdminTicketInput {
  request_id: string;
  source: TicketSource;
  text: string;
  taxonomy_version: "invoiceops-v1";
  metadata?: { channel?: "supplier_portal" | "email" | "internal"; locale?: "zh" | "en" | "mixed" };
}

export interface AdminTicket {
  ticket_id: string;
  request_id: string;
  source: TicketSource;
  sanitized_text: string;
  language: "zh" | "en" | "mixed" | "unknown";
  risk: TicketRisk;
  status: TicketStatus;
  labels: string[];
  primary_queue: string;
  created_at: string;
}

export interface AdminDeleteResponse {
  deleted_count: number;
  ticket_ids: string[];
  trace_id: string;
}

export interface AdminBatchOverwriteResponse {
  overwritten_count: number;
  items: ClassificationResponse[];
  trace_id: string;
}

export interface BatchStatus {
  batch_id: string;
  status: "queued" | "processing" | "completed" | "failed" | "cancelled";
  total_rows: number;
  processed_rows: number;
  success_rows: number;
  failed_rows: number;
  row_errors: Array<{ row_number: number; code: string; message: string }>;
  mock?: boolean;
}

export interface ReviewItem {
  ticket_id: string;
  request_id: string;
  risk: "low" | "medium" | "high";
  decision: "needs_review";
  primary_queue: string;
  collaborator_queues: string[];
  predictions: LabelScore[];
  reason_codes: string[];
  sanitized_text: string;
  waiting_seconds: number;
  overdue: boolean;
  review_sla_minutes: number;
}

export interface ReviewHistoryItem {
  ticket_id: string;
  revision: number;
  labels: string[];
  primary_queue: string;
  note: string;
  reviewer_id: string;
  training_candidate: boolean;
  created_at: string;
  trace_id: string;
}

export interface ReviewDecisionResponse {
  ticket_id: string;
  revision: number;
  labels: string[];
  reviewer_id: string;
  trace_id: string;
  mock?: boolean;
}

export interface AuditEvent {
  event_id: string;
  event_type: string;
  event_version: string;
  occurred_at: string;
  request_id: string;
  trace_id: string;
  actor: { id?: string };
  subject: { id?: string };
  payload: Record<string, unknown>;
}

export interface TaxonomyResponse {
  taxonomy_version: string;
  routing_version: string;
  labels: Array<{ code?: string; label_code?: string; name?: string; risk?: string }>;
}

export interface ApiErrorShape {
  code?: string;
  message: string;
  trace_id?: string;
}
