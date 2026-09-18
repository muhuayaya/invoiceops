import { mockAdminTickets, mockAudit, mockBatch, mockClassification, mockMetricsText, mockReviews, mockTaxonomy } from "./mock";
import type { AdminBatchOverwriteResponse, AdminDeleteResponse, AdminTicket, AdminTicketInput, ApiErrorShape, AuditEvent, BatchStatus, ClassificationResponse, ReviewDecisionResponse, ReviewHistoryItem, ReviewItem, Session, TaxonomyResponse } from "./types";

let localAdminTickets: AdminTicket[] = [...mockAdminTickets];

export class ApiError extends Error {
  readonly status: number;
  readonly details: ApiErrorShape;

  constructor(status: number, details: ApiErrorShape) {
    super(details.message);
    this.name = "ApiError";
    this.status = status;
    this.details = details;
  }
}

export class ApiClient {
  readonly baseUrl: string;
  readonly mode: "api" | "local-demo";
  private readonly session: Session | null;

  constructor(session: Session | null) {
    this.baseUrl = (import.meta.env.VITE_API_BASE_URL ?? "").trim().replace(/\/$/, "");
    this.mode = this.baseUrl ? "api" : "local-demo";
    this.session = session;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    if (!this.baseUrl) throw new ApiError(0, { code: "LOCAL_DEMO_MODE", message: "未配置 VITE_API_BASE_URL，当前为本地演示模式。" });
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
    if (this.session?.accessToken) headers.set("Authorization", `Bearer ${this.session.accessToken}`);
    const response = await fetch(`${this.baseUrl}${path}`, { ...init, headers });
    const raw = await response.text();
    let body: unknown = null;
    try { body = raw ? JSON.parse(raw) : null; } catch { body = raw; }
    if (!response.ok) {
      const details = typeof body === "object" && body !== null ? body as ApiErrorShape : { message: raw || `请求失败（${response.status}）` };
      throw new ApiError(response.status, details);
    }
    return body as T;
  }

  async login(username: string, password: string): Promise<Session> {
    if (!this.baseUrl) {
      if (!["admin", "reviewer", "observer"].includes(username) || password !== username) throw new ApiError(401, { message: "本地演示账号密码相同：admin / reviewer / observer。" });
      return { accessToken: `local-demo-${username}`, username, role: username as Session["role"], mode: "local-demo" };
    }
    const response = await this.request<{ access_token: string; role: Session["role"] }>("/api/v1/auth/token", { method: "POST", body: JSON.stringify({ username, password }) });
    return { accessToken: response.access_token, username, role: response.role, mode: "api" };
  }

  async classify(payload: { request_id: string; source: string; text: string; taxonomy_version: string; metadata: { channel: string; locale: string } }): Promise<ClassificationResponse> {
    if (!this.baseUrl) return { ...mockClassification, request_id: payload.request_id, mock: true };
    return this.request<ClassificationResponse>("/api/v1/classifications", { method: "POST", headers: { "Idempotency-Key": `web-${payload.request_id}` }, body: JSON.stringify(payload) });
  }

  async adminCreateTicket(payload: AdminTicketInput): Promise<ClassificationResponse> {
    if (!this.baseUrl) {
      if (this.session?.role !== "admin") throw new ApiError(403, { code: "FORBIDDEN", message: "当前账号没有执行此操作的权限。" });
      if (localAdminTickets.some(item => item.request_id === payload.request_id)) throw new ApiError(409, { code: "IDEMPOTENCY_CONFLICT", message: "请求编号已存在，请更换请求编号后重试。" });
      const response = { ...mockClassification, request_id: payload.request_id, trace_id: `local-admin-${Date.now()}`, mock: true };
      localAdminTickets = [{
        ticket_id: `local-admin-ticket-${Date.now()}`,
        request_id: payload.request_id,
        source: payload.source,
        sanitized_text: payload.text,
        language: response.language,
        risk: response.decision === "needs_review" ? "high" : "low",
        status: response.decision,
        labels: response.predictions.map(item => item.label_code),
        primary_queue: response.route.primary,
        created_at: new Date().toISOString(),
      }, ...localAdminTickets];
      return response;
    }
    return this.request<ClassificationResponse>("/api/v1/admin/tickets", { method: "POST", headers: { "Idempotency-Key": `web-admin-${payload.request_id}` }, body: JSON.stringify(payload) });
  }

  async adminListTickets(filters: { request_id?: string; risk?: string; status?: string } = {}): Promise<AdminTicket[]> {
    if (!this.baseUrl) {
      if (this.session?.role !== "admin") throw new ApiError(403, { code: "FORBIDDEN", message: "当前账号没有执行此操作的权限。" });
      return localAdminTickets.filter(item => (!filters.request_id || item.request_id.toLowerCase().includes(filters.request_id.toLowerCase())) && (!filters.risk || item.risk === filters.risk) && (!filters.status || item.status === filters.status));
    }
    const query = new URLSearchParams({ limit: "100" });
    if (filters.request_id) query.set("request_id", filters.request_id);
    if (filters.risk) query.set("risk", filters.risk);
    if (filters.status) query.set("status", filters.status);
    const response = await this.request<{ items: AdminTicket[] }>(`/api/v1/admin/tickets?${query.toString()}`);
    return response.items;
  }

  async adminDeleteTickets(ticketIds: string[]): Promise<AdminDeleteResponse> {
    if (!this.baseUrl) {
      if (this.session?.role !== "admin") throw new ApiError(403, { code: "FORBIDDEN", message: "当前账号没有执行此操作的权限。" });
      if (!ticketIds.length || ticketIds.some(id => !localAdminTickets.some(item => item.ticket_id === id))) throw new ApiError(404, { code: "NOT_FOUND", message: "工单不存在。" });
      localAdminTickets = localAdminTickets.filter(item => !ticketIds.includes(item.ticket_id));
      return { deleted_count: ticketIds.length, ticket_ids: ticketIds, trace_id: `local-admin-delete-${Date.now()}` };
    }
    return this.request<AdminDeleteResponse>("/api/v1/admin/tickets/batch-delete", { method: "POST", headers: { "Idempotency-Key": `web-admin-delete-${Date.now()}` }, body: JSON.stringify({ ticket_ids: ticketIds }) });
  }

  async adminDeleteTicket(ticketId: string): Promise<AdminDeleteResponse> {
    if (!this.baseUrl) return this.adminDeleteTickets([ticketId]);
    return this.request<AdminDeleteResponse>(`/api/v1/admin/tickets/${encodeURIComponent(ticketId)}`, { method: "DELETE", headers: { "Idempotency-Key": `web-admin-single-delete-${Date.now()}` } });
  }

  async adminOverwriteTickets(items: AdminTicketInput[]): Promise<AdminBatchOverwriteResponse> {
    if (!this.baseUrl) {
      if (this.session?.role !== "admin") throw new ApiError(403, { code: "FORBIDDEN", message: "当前账号没有执行此操作的权限。" });
      if (items.some(item => !localAdminTickets.some(ticket => ticket.request_id === item.request_id))) throw new ApiError(404, { code: "NOT_FOUND", message: "覆盖目标工单不存在。" });
      const responses = items.map(item => ({ ...mockClassification, request_id: item.request_id, trace_id: `local-admin-overwrite-${Date.now()}`, mock: true }));
      localAdminTickets = localAdminTickets.map(ticket => {
        const item = items.find(candidate => candidate.request_id === ticket.request_id);
        if (!item) return ticket;
        const response = responses.find(candidate => candidate.request_id === ticket.request_id) ?? mockClassification;
        return { ...ticket, source: item.source, sanitized_text: item.text, language: response.language, risk: response.decision === "needs_review" ? "high" : "low", status: response.decision, labels: response.predictions.map(prediction => prediction.label_code), primary_queue: response.route.primary, created_at: new Date().toISOString() };
      });
      return { overwritten_count: responses.length, items: responses, trace_id: `local-admin-overwrite-${Date.now()}` };
    }
    return this.request<AdminBatchOverwriteResponse>("/api/v1/admin/tickets/batch-overwrite", { method: "POST", headers: { "Idempotency-Key": `web-admin-overwrite-${Date.now()}` }, body: JSON.stringify({ items }) });
  }

  async submitBatch(file: File): Promise<BatchStatus> {
    if (!this.baseUrl) return { ...mockBatch, batch_id: `local-${Date.now()}`, mock: true };
    const form = new FormData();
    form.append("file", file);
    return this.request<BatchStatus>("/api/v1/batches", { method: "POST", headers: { "Idempotency-Key": `web-batch-${Date.now()}` }, body: form });
  }

  async getBatch(batchId: string): Promise<BatchStatus> {
    if (!this.baseUrl) return mockBatch;
    return this.request<BatchStatus>(`/api/v1/batches/${encodeURIComponent(batchId)}`);
  }

  async listReviews(filters: { risk?: string; label_code?: string; waiting_min_seconds?: number }): Promise<ReviewItem[]> {
    if (!this.baseUrl) return mockReviews;
    const query = new URLSearchParams({ limit: "100" });
    if (filters.risk) query.set("risk", filters.risk);
    if (filters.label_code) query.set("label_code", filters.label_code);
    if (filters.waiting_min_seconds !== undefined) query.set("waiting_min_seconds", String(filters.waiting_min_seconds));
    const response = await this.request<{ items: ReviewItem[] }>(`/api/v1/reviews?${query.toString()}`);
    return response.items;
  }

  async reviewHistory(ticketId: string): Promise<ReviewHistoryItem[]> {
    if (!this.baseUrl) return [];
    const response = await this.request<{ items: ReviewHistoryItem[] }>(`/api/v1/reviews/${encodeURIComponent(ticketId)}/history`);
    return response.items;
  }

  async submitReview(ticketId: string, payload: { labels: string[]; primary_queue: string; note: string }): Promise<ReviewDecisionResponse> {
    if (!this.baseUrl) return { ticket_id: ticketId, revision: 1, labels: payload.labels, reviewer_id: this.session?.username ?? "local-reviewer", trace_id: `local-review-${Date.now()}`, mock: true };
    return this.request<ReviewDecisionResponse>(`/api/v1/reviews/${encodeURIComponent(ticketId)}/decisions`, { method: "POST", body: JSON.stringify(payload) });
  }

  async taxonomy(): Promise<TaxonomyResponse> {
    if (!this.baseUrl) return mockTaxonomy;
    return this.request<TaxonomyResponse>("/api/v1/taxonomies/current");
  }

  async audit(requestId: string): Promise<AuditEvent[]> {
    if (!this.baseUrl) {
      if (requestId !== mockClassification.request_id) throw new ApiError(404, { message: "本地演示数据中没有该 request_id。" });
      return mockAudit;
    }
    const response = await this.request<{ request_id: string; events: AuditEvent[] }>(`/api/v1/audit/${encodeURIComponent(requestId)}`);
    return response.events;
  }

  async metrics(): Promise<string> {
    if (!this.baseUrl) return mockMetricsText;
    const response = await fetch(`${this.baseUrl}/metrics`, { headers: this.session?.accessToken ? { Authorization: `Bearer ${this.session.accessToken}` } : undefined });
    if (!response.ok) throw new ApiError(response.status, { message: `指标请求失败（${response.status}）` });
    return response.text();
  }
}
