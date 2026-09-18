import { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiClient } from "./api";
import { decisionName, errorMessage, eventName, labelName, languageName, metricName, modeName, reasonName, riskName, roleName, rowErrorCode, rowErrorMessage, routeCodes, routeName, sourceName, statusName } from "./i18n";
import type { AdminTicket, AdminTicketInput, AuditEvent, BatchStatus, ClassificationResponse, ReviewHistoryItem, ReviewItem, Session, TicketSource } from "./types";
import "./styles.css";

type Page = "classify" | "batch" | "reviews" | "audit" | "metrics" | "data";

const labels = ["PRICE_VARIANCE", "QUANTITY_RECEIPT_VARIANCE", "TAX_CURRENCY_AMOUNT", "DUPLICATE_INVOICE", "MISSING_PO_OR_RECEIPT", "PAYMENT_STATUS", "SUPPLIER_MASTER_CHANGE", "OTHER_REVIEW"];

function App() {
  const [session, setSession] = useState<Session | null>(() => {
    try { return JSON.parse(localStorage.getItem("invoiceops-session") ?? "null") as Session | null; } catch { return null; }
  });
  const [page, setPage] = useState<Page>("classify");
  const [error, setError] = useState("");
  const api = useMemo(() => new ApiClient(session), [session]);

  const login = async (username: string, password: string) => {
    setError("");
    try {
      const next = await new ApiClient(null).login(username, password);
      localStorage.setItem("invoiceops-session", JSON.stringify(next));
      setSession(next);
    } catch (cause) { setError(errorMessage(cause, "登录失败，请检查账号和密码。")); }
  };
  const logout = () => { localStorage.removeItem("invoiceops-session"); setSession(null); };

  if (!session) return <Login error={error} onLogin={login} />;
  return <div className="app-shell">
    <header className="topbar"><a className="brand" href="#classify" onClick={() => setPage("classify")}>InvoiceOps<small>多语言工单分流控制台</small></a><div className="user-tools"><span>{session.username} · {roleName(session.role)} · {modeName(session.mode)}</span><button className="button secondary" onClick={logout}>退出</button></div></header>
    <div className="layout"><aside className="sidebar"><p className="nav-label">工作台</p><nav className="nav">
      {([["classify", "单条分类"], ["batch", "批次处理"], ["reviews", "人工复核"], ["audit", "审计追踪"], ["metrics", "运行指标"], ...(session.role === "admin" ? [["data", "数据管理"]] : [])] as [Page, string][]).map(([key, text]) => <button key={key} aria-current={page === key ? "page" : undefined} onClick={() => { setError(""); setPage(key); }}>{text}</button>)}
    </nav><p className="muted small" style={{ margin: "28px 12px" }}>登录后才能访问受保护页面。未配置后端接口地址时使用本地演示数据。</p></aside>
    <main className="content">{error && <div className="notice error" role="alert">{error}</div>}{page === "classify" && <ClassifyPage api={api} onError={setError} />}{page === "batch" && <BatchPage api={api} onError={setError} />}{page === "reviews" && <ReviewsPage api={api} onError={setError} />}{page === "audit" && <AuditPage api={api} onError={setError} />}{page === "metrics" && <MetricsPage api={api} onError={setError} />}{page === "data" && session.role === "admin" && <DataManagementPage api={api} onError={setError} />}</main>
    </div>
  </div>;
}

function Login({ error, onLogin }: { error: string; onLogin: (username: string, password: string) => Promise<void> }) {
  const [username, setUsername] = useState("reviewer"); const [password, setPassword] = useState("reviewer");
  return <main className="login-page"><section className="card login-card"><div className="brand">InvoiceOps<small>多语言工单分流控制台</small></div><h1>进入运营工作台</h1><p className="lede">使用本地演示账号，或连接后端接口进行真实操作。</p>{error && <div className="notice error" role="alert">{error}</div>}<form onSubmit={e => { e.preventDefault(); void onLogin(username, password); }}><div className="field"><label htmlFor="username">账号</label><input id="username" value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" /></div><div className="field"><label htmlFor="password">密码</label><input id="password" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" /></div><button className="button" type="submit">登录</button></form><p className="muted small">开发环境演示账号：admin / reviewer / observer（密码同名，生产环境禁用）</p></section></main>;
}

function ClassifyPage({ api, onError }: { api: ApiClient; onError: (message: string) => void }) {
  const [text, setText] = useState("发票金额不正确，而且我们还没有收到付款，请协助核查。"); const [source, setSource] = useState("email"); const [result, setResult] = useState<ClassificationResponse | null>(null); const [busy, setBusy] = useState(false);
  const submit = async () => { setBusy(true); onError(""); try { setResult(await api.classify({ request_id: `web-${Date.now()}`, source, text, taxonomy_version: "invoiceops-v1", metadata: { channel: "email", locale: "mixed" } })); } catch (cause) { onError(errorMessage(cause, "分类失败，请稍后重试。")); } finally { setBusy(false); } };
  return <><div className="page-heading"><div><p className="eyebrow">分类处理</p><h1>单条工单分类</h1><p className="lede">提交中英混合正文，查看多标签分数、路由和人工复核决策。</p></div></div><div className="grid grid-2"><section className="card"><div className="field"><label htmlFor="source">来源</label><select id="source" value={source} onChange={e => setSource(e.target.value)}><option value="email">{sourceName("email")}</option><option value="portal">{sourceName("portal")}</option><option value="api">{sourceName("api")}</option><option value="manual">{sourceName("manual")}</option></select></div><div className="field"><label htmlFor="ticket-text">工单正文（最多 5,000 字符）</label><textarea id="ticket-text" value={text} onChange={e => setText(e.target.value)} maxLength={5000} /></div><div className="form-actions"><button className="button" onClick={() => void submit()} disabled={busy || !text.trim()}>{busy ? "分类中…" : "提交分类"}</button><span className="muted small">字符数：{text.length}/5000</span></div></section>{result ? <ClassificationResult result={result} /> : <section className="card"><h2>结果预览</h2><p className="empty">提交后将在此显示模型、阈值、语言和路由版本。</p></section>}</div></>;
}

function ClassificationResult({ result }: { result: ClassificationResponse }) {
  return <section className="card"><div className="page-heading"><div><h2>分类结果</h2><span className={`badge ${result.decision === "needs_review" ? "high" : "low"}`}>{decisionName(result.decision)}</span></div><span className="mono">{result.trace_id}</span></div><dl className="result-grid"><div className="detail"><dt>语言</dt><dd>{languageName(result.language)}</dd></div><div className="detail"><dt>责任队列</dt><dd>{routeName(result.route.primary)}</dd></div><div className="detail"><dt>模型版本</dt><dd className="mono">{result.model_version}</dd></div><div className="detail"><dt>阈值 / 标签版本</dt><dd className="mono">{result.threshold_version} / {result.taxonomy_version}</dd></div></dl><h3 style={{ marginTop: 18 }}>标签分数</h3><div className="chips">{result.predictions.map(item => <span className="badge" key={item.label_code}>{labelName(item.label_code)} <span className="score">{(item.score * 100).toFixed(1)}%</span></span>)}</div>{result.reason_codes?.length ? <p className="notice warning" style={{ marginTop: 18, marginBottom: 0 }}>复核原因：{result.reason_codes.map(reasonName).join(" · ")}</p> : null}</section>;
}

function BatchPage({ api, onError }: { api: ApiClient; onError: (message: string) => void }) {
  const [batch, setBatch] = useState<BatchStatus | null>(null); const [busy, setBusy] = useState(false);
  const submit = async (file: File) => { setBusy(true); onError(""); try { setBatch(await api.submitBatch(file)); } catch (cause) { onError(errorMessage(cause, "批次提交失败，请检查文件后重试。")); } finally { setBusy(false); } };
  useEffect(() => {
    if (!batch || !["queued", "processing"].includes(batch.status)) return;
    let disposed = false;
    const poll = async () => {
      try {
        const next = await api.getBatch(batch.batch_id);
        if (!disposed) { setBatch(next); onError(""); }
      } catch (cause) {
        if (!disposed) onError(errorMessage(cause, "批次状态查询失败，请稍后重试。"));
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [api, batch?.batch_id, batch?.status, onError]);
  const pending = batch?.status === "queued" || batch?.status === "processing";
  return <><div className="page-heading"><div><p className="eyebrow">批次接入</p><h1>批次处理</h1><p className="lede">上传 UTF-8 格式的 CSV 文件，查看有效行、失败行和逐行原因。</p></div></div><section className="card"><div className="field"><label htmlFor="batch-file">批次文件（CSV）</label><input id="batch-file" type="file" accept=".csv,text/csv" disabled={busy} onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) void submit(file); }} />{busy ? <p className="muted small" role="status">正在上传批次文件…</p> : pending ? <p className="muted small" role="status">批次已提交，后台处理中，页面会自动刷新。</p> : null}</div>{batch ? <><div className="grid grid-3"><Stat label="状态" value={statusName(batch.status)} /><Stat label="处理进度" value={`${batch.processed_rows}/${batch.total_rows}`} /><Stat label="失败行数" value={String(batch.failed_rows)} /></div>{batch.row_errors.length > 0 && <div className="table-wrap" style={{ marginTop: 18 }}><table><thead><tr><th>行号</th><th>错误类型</th><th>原因</th></tr></thead><tbody>{batch.row_errors.map(e => <tr key={`${e.row_number}-${e.code}`}><td>{e.row_number}</td><td className="mono">{rowErrorCode(e.code)}</td><td>{rowErrorMessage(e.message)}</td></tr>)}</tbody></table></div>}</> : <p className="empty">尚未提交批次。</p>}</section></>;
}

function ReviewsPage({ api, onError }: { api: ApiClient; onError: (message: string) => void }) {
  const [items, setItems] = useState<ReviewItem[]>([]); const [selected, setSelected] = useState<ReviewItem | null>(null); const [history, setHistory] = useState<ReviewHistoryItem[]>([]); const [note, setNote] = useState(""); const [chosen, setChosen] = useState<string[]>([]); const [chosenQueue, setChosenQueue] = useState("FINANCE_TAX"); const [riskFilter, setRiskFilter] = useState(""); const [labelFilter, setLabelFilter] = useState(""); const [waitingFilter, setWaitingFilter] = useState("");
  const load = async () => { try { const next = await api.listReviews({ risk: riskFilter || undefined, label_code: labelFilter || undefined, waiting_min_seconds: waitingFilter ? Number(waitingFilter) : undefined }); setItems([...next].sort((a, b) => (a.risk === "high" ? -1 : b.risk === "high" ? 1 : 0))); if (!selected && next[0]) { setSelected(next[0]); setChosen(next[0].predictions.map(p => p.label_code)); setChosenQueue(next[0].primary_queue); } } catch (cause) { onError(errorMessage(cause, "复核队列加载失败，请稍后重试。")); } };
  useEffect(() => { void load(); }, []);
  useEffect(() => { if (!selected) { setHistory([]); return; } void api.reviewHistory(selected.ticket_id).then(setHistory).catch(() => setHistory([])); }, [api, selected?.ticket_id]);
  const save = async () => { if (!selected) return; try { await api.submitReview(selected.ticket_id, { labels: chosen.length ? chosen : ["OTHER_REVIEW"], primary_queue: chosenQueue, note }); await load(); setNote(""); } catch (cause) { onError(errorMessage(cause, "复核提交失败，请稍后重试。")); } };
  return <><div className="page-heading"><div><p className="eyebrow">人工复核</p><h1>人工复核队列</h1><p className="lede">高风险工单优先，人工决策以追加修订方式保存。</p></div><button className="button secondary" onClick={() => void load()}>刷新</button></div><section className="card review-filters"><div className="field"><label htmlFor="review-risk">风险筛选</label><select id="review-risk" value={riskFilter} onChange={e => setRiskFilter(e.target.value)}><option value="">全部风险</option><option value="high">{riskName("high")}</option><option value="medium">{riskName("medium")}</option><option value="low">{riskName("low")}</option></select></div><div className="field"><label htmlFor="review-label">标签筛选</label><select id="review-label" value={labelFilter} onChange={e => setLabelFilter(e.target.value)}><option value="">全部标签</option>{labels.map(label => <option key={label} value={label}>{labelName(label)}</option>)}</select></div><div className="field"><label htmlFor="review-waiting">等待时间</label><select id="review-waiting" value={waitingFilter} onChange={e => setWaitingFilter(e.target.value)}><option value="">全部</option><option value="1800">超过 30 分钟</option><option value="3600">超过 1 小时</option></select></div><button className="button secondary filter-button" onClick={() => void load()}>应用筛选</button></section><div className="review-layout"><section className="review-list">{items.map(item => <button className={`review-item ${selected?.ticket_id === item.ticket_id ? "selected" : ""}`} key={item.ticket_id} onClick={() => { setSelected(item); setChosen(item.predictions.map(p => p.label_code)); setChosenQueue(item.primary_queue); }}><span className={`badge ${item.risk}`}>{riskName(item.risk)}</span>{item.overdue && <span className="badge high" style={{ marginLeft: 8 }}>已逾期</span>}<strong style={{ display: "block", marginTop: 8 }}>{item.request_id}</strong><p>{item.sanitized_text}</p><span className="muted small">已等待 {Math.floor(item.waiting_seconds / 60)} 分钟</span></button>)}{!items.length && <div className="card empty">当前没有待复核工单。</div>}</section><section className="card review-detail">{selected ? <><h2>{selected.request_id}</h2><p>{selected.sanitized_text}</p><p className="muted small">已等待 {Math.floor(selected.waiting_seconds / 60)} 分钟{selected.overdue ? "，已超过高风险复核时限" : ""}</p><h3>标签</h3><div className="check-grid">{labels.map(label => <label key={label}><input type="checkbox" checked={chosen.includes(label)} onChange={e => setChosen(e.target.checked ? [...chosen, label] : chosen.filter(item => item !== label))} />{labelName(label)}</label>)}</div><div className="field"><label htmlFor="review-queue">主责任队列</label><select id="review-queue" value={chosenQueue} onChange={e => setChosenQueue(e.target.value)}>{routeCodes.map(queue => <option key={queue} value={queue}>{routeName(queue)}</option>)}</select></div><div className="field" style={{ marginTop: 16 }}><label htmlFor="review-note">复核备注</label><textarea id="review-note" value={note} onChange={e => setNote(e.target.value)} /></div><button className="button" onClick={() => void save()}>追加复核决策</button>{history.length > 0 && <div style={{ marginTop: 24 }}><h3>修订历史</h3>{history.map(item => <div className="notice info" key={`${item.ticket_id}-${item.revision}`}><strong>第 {item.revision} 次 · {item.reviewer_id}</strong><span className="muted small"> · {item.created_at}</span><div>{item.labels.map(labelName).join("、")} · {routeName(item.primary_queue)}</div>{item.note && <div>{item.note}</div>}</div>)}</div>}</> : <p className="empty">选择一条工单查看详情。</p>}</section></div></>;
}

function DataManagementPage({ api, onError }: { api: ApiClient; onError: (message: string) => void }) {
  const [items, setItems] = useState<AdminTicket[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [requestFilter, setRequestFilter] = useState("");
  const [riskFilter, setRiskFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [requestId, setRequestId] = useState(() => `admin-${Date.now()}`);
  const [source, setSource] = useState<TicketSource>("manual");
  const [text, setText] = useState("");
  const [overwriteItems, setOverwriteItems] = useState<AdminTicketInput[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setBusy(true);
    onError("");
    try {
      setItems(await api.adminListTickets({ request_id: requestFilter || undefined, risk: riskFilter || undefined, status: statusFilter || undefined }));
      setSelectedIds([]);
    } catch (cause) {
      onError(errorMessage(cause, "数据列表加载失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => { void load(); }, []);

  const create = async () => {
    setBusy(true);
    onError("");
    setMessage("");
    try {
      await api.adminCreateTicket({ request_id: requestId.trim(), source, text: text.trim(), taxonomy_version: "invoiceops-v1", metadata: { locale: "mixed" } });
      setMessage("工单已录入、脱敏并完成分类。");
      setRequestId(`admin-${Date.now()}`);
      setText("");
      await load();
    } catch (cause) {
      onError(errorMessage(cause, "工单录入失败，请检查请求编号和正文。"));
    } finally {
      setBusy(false);
    }
  };

  const deleteSelected = async () => {
    if (!selectedIds.length || !window.confirm(`确认删除选中的 ${selectedIds.length} 条工单吗？此操作不可恢复。`)) return;
    setBusy(true);
    onError("");
    setMessage("");
    try {
      const result = await api.adminDeleteTickets(selectedIds);
      setMessage(`已删除 ${result.deleted_count} 条工单。`);
      await load();
    } catch (cause) {
      onError(errorMessage(cause, "删除失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  };

  const deleteOne = async (ticket: AdminTicket) => {
    if (!window.confirm(`确认删除请求编号“${ticket.request_id}”吗？此操作不可恢复。`)) return;
    try {
      await api.adminDeleteTicket(ticket.ticket_id);
      setMessage(`已删除工单“${ticket.request_id}”。`);
      await load();
    } catch (cause) {
      onError(errorMessage(cause, "删除失败，请稍后重试。"));
    }
  };

  const readOverwriteFile = async (file: File) => {
    try {
      const parsed = parseOverwriteCsv(await file.text());
      setOverwriteItems(parsed);
      onError("");
      setMessage(`已读取 ${parsed.length} 条覆盖数据，请确认后提交。`);
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : "覆盖 CSV 读取失败，请检查表头和内容。");
      setOverwriteItems([]);
    }
  };

  const overwrite = async () => {
    if (!overwriteItems.length) return;
    setBusy(true);
    onError("");
    setMessage("");
    try {
      const result = await api.adminOverwriteTickets(overwriteItems);
      setMessage(`已批量覆盖 ${result.overwritten_count} 条工单，并重新完成分类。`);
      setOverwriteItems([]);
      await load();
    } catch (cause) {
      onError(errorMessage(cause, "批量覆盖失败，未完成的目标不会被修改。"));
    } finally {
      setBusy(false);
    }
  };

  const toggleAll = () => setSelectedIds(selectedIds.length === items.length ? [] : items.map(item => item.ticket_id));
  const toggleOne = (ticketId: string) => setSelectedIds(selectedIds.includes(ticketId) ? selectedIds.filter(id => id !== ticketId) : [...selectedIds, ticketId]);
  return <><div className="page-heading"><div><p className="eyebrow">管理员工作台</p><h1>数据管理</h1><p className="lede">仅管理员可录入、筛选、删除和批量覆盖工单；列表只展示已脱敏正文。</p></div><button className="button secondary" onClick={() => void load()} disabled={busy}>刷新列表</button></div>{message && <div className="notice success" role="status">{message}</div>}<div className="grid grid-2"><section className="card"><h2>手工录入</h2><div className="field"><label htmlFor="admin-request-id">请求编号</label><input id="admin-request-id" value={requestId} onChange={e => setRequestId(e.target.value)} maxLength={128} /></div><div className="field"><label htmlFor="admin-source">来源</label><select id="admin-source" value={source} onChange={e => setSource(e.target.value as TicketSource)}><option value="email">{sourceName("email")}</option><option value="portal">{sourceName("portal")}</option><option value="api">{sourceName("api")}</option><option value="manual">{sourceName("manual")}</option></select></div><div className="field"><label htmlFor="admin-text">工单正文</label><textarea id="admin-text" value={text} onChange={e => setText(e.target.value)} maxLength={5000} placeholder="请输入供应商或财务工单正文" /></div><div className="form-actions"><button className="button" onClick={() => void create()} disabled={busy || !requestId.trim() || !text.trim()}>录入并分类</button><span className="muted small">正文会先脱敏，再进入分类与路由。</span></div></section><section className="card"><h2>批量覆盖</h2><p className="muted small">CSV 表头必须为：request_id,source,text,taxonomy_version。只允许覆盖已存在的请求编号，整批校验通过后才会执行。</p><div className="field"><label htmlFor="overwrite-file">覆盖 CSV</label><input id="overwrite-file" type="file" accept=".csv,text/csv" disabled={busy} onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) void readOverwriteFile(file); }} /></div>{overwriteItems.length > 0 && <div className="notice info">待覆盖 {overwriteItems.length} 条：{overwriteItems.map(item => item.request_id).join("、")}</div>}<button className="button" onClick={() => void overwrite()} disabled={busy || !overwriteItems.length}>确认批量覆盖</button></section></div><section className="card"><div className="page-heading"><div><h2>工单列表</h2><p className="muted small">删除会移除当前工单及其分类结果，审计记录仍会保留。</p></div><button className="button danger" onClick={() => void deleteSelected()} disabled={busy || !selectedIds.length}>批量删除（{selectedIds.length}）</button></div><section className="grid grid-3"><div className="field"><label htmlFor="data-request-filter">请求编号筛选</label><input id="data-request-filter" value={requestFilter} onChange={e => setRequestFilter(e.target.value)} placeholder="支持模糊匹配" /></div><div className="field"><label htmlFor="data-risk-filter">风险筛选</label><select id="data-risk-filter" value={riskFilter} onChange={e => setRiskFilter(e.target.value)}><option value="">全部风险</option><option value="high">{riskName("high")}</option><option value="medium">{riskName("medium")}</option><option value="low">{riskName("low")}</option></select></div><div className="field"><label htmlFor="data-status-filter">状态筛选</label><select id="data-status-filter" value={statusFilter} onChange={e => setStatusFilter(e.target.value)}><option value="">全部状态</option><option value="classified">{statusName("classified")}</option><option value="needs_review">{statusName("needs_review")}</option><option value="reviewed">{statusName("reviewed")}</option></select></div></section><button className="button secondary" onClick={() => void load()} disabled={busy}>应用筛选</button><div className="table-wrap" style={{ marginTop: 18 }}><table><thead><tr><th><input type="checkbox" aria-label="全选工单" checked={items.length > 0 && selectedIds.length === items.length} onChange={toggleAll} /></th><th>请求编号</th><th>来源</th><th>脱敏正文</th><th>语言</th><th>风险</th><th>状态</th><th>标签</th><th>责任队列</th><th>录入时间</th><th>操作</th></tr></thead><tbody>{items.map(item => <tr key={item.ticket_id}><td><input type="checkbox" aria-label={`选择 ${item.request_id}`} checked={selectedIds.includes(item.ticket_id)} onChange={() => toggleOne(item.ticket_id)} /></td><td className="mono">{item.request_id}</td><td>{sourceName(item.source)}</td><td>{item.sanitized_text}</td><td>{languageName(item.language)}</td><td><span className={`badge ${item.risk}`}>{riskName(item.risk)}</span></td><td>{statusName(item.status)}</td><td>{item.labels.map(labelName).join("、") || "—"}</td><td>{routeName(item.primary_queue)}</td><td className="small">{item.created_at}</td><td><button className="button danger" onClick={() => void deleteOne(item)} disabled={busy}>删除</button></td></tr>)}</tbody></table>{!items.length && <p className="empty">暂无符合条件的工单。</p>}</div></section></>;
}

function parseOverwriteCsv(content: string): AdminTicketInput[] {
  const rows = parseCsvRows(content);
  if (!rows.length) throw new Error("覆盖 CSV 不能为空。");
  const headers = rows.shift()?.map(value => value.trim().toLowerCase()) ?? [];
  const required = ["request_id", "source", "text", "taxonomy_version"];
  if (!required.every(name => headers.includes(name))) throw new Error("覆盖 CSV 必须包含：request_id、source、text、taxonomy_version。");
  const values = rows.map(row => Object.fromEntries(headers.map((header, index) => [header, row[index] ?? ""])));
  return values.filter(row => Object.values(row).some(value => value.trim())).map(row => ({
    request_id: row.request_id.trim(),
    source: row.source.trim() as TicketSource,
    text: row.text.trim(),
    taxonomy_version: row.taxonomy_version.trim() as "invoiceops-v1",
    metadata: { locale: "mixed" },
  }));
}

function parseCsvRows(content: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let value = "";
  let quoted = false;
  for (let index = 0; index < content.length; index += 1) {
    const char = content[index];
    if (char === '"' && quoted && content[index + 1] === '"') { value += '"'; index += 1; continue; }
    if (char === '"') { quoted = !quoted; continue; }
    if (char === "," && !quoted) { row.push(value); value = ""; continue; }
    if ((char === "\n" || char === "\r") && !quoted) { if (char === "\r" && content[index + 1] === "\n") index += 1; row.push(value); if (row.some(cell => cell.trim())) rows.push(row); row = []; value = ""; continue; }
    value += char;
  }
  row.push(value);
  if (row.some(cell => cell.trim())) rows.push(row);
  return rows;
}

function AuditPage({ api, onError }: { api: ApiClient; onError: (message: string) => void }) { const [requestId, setRequestId] = useState("demo-request-001"); const [events, setEvents] = useState<AuditEvent[]>([]); const load = async () => { try { setEvents(await api.audit(requestId)); } catch (cause) { onError(errorMessage(cause, "审计查询失败，请检查请求编号。")); } }; return <><div className="page-heading"><div><p className="eyebrow">审计追踪</p><h1>审计时间线</h1><p className="lede">按请求编号查看接入、预测和路由事件。</p></div></div><section className="card"><div className="field"><label htmlFor="request-id">请求编号</label><div className="form-actions"><input id="request-id" value={requestId} onChange={e => setRequestId(e.target.value)} aria-label="请求编号" /><button className="button" onClick={() => void load()}>查询</button></div></div>{events.length ? <div className="timeline">{events.map(event => <div className="timeline-item" key={event.event_id}><strong>{eventName(event.event_type)}</strong><span className="muted small"> · {event.occurred_at}</span><p>追踪编号：{event.trace_id}</p></div>)}</div> : <p className="empty">输入请求编号查询审计事件。</p>}</section></>; }

function MetricsPage({ api, onError }: { api: ApiClient; onError: (message: string) => void }) { const [text, setText] = useState(""); useEffect(() => { api.metrics().then(setText).catch(cause => onError(errorMessage(cause, "指标加载失败，请稍后重试。"))); }, [api, onError]); const metrics = text.split("\n").filter(line => line && !line.startsWith("#")).map(line => { const [name, value] = line.split(/\s+/, 2); return { name, value }; }); return <><div className="page-heading"><div><p className="eyebrow">运行监控</p><h1>运行指标</h1><p className="lede">只读查看工单量、审计量和运行时指标。</p></div></div><div className="grid grid-3">{metrics.map(metric => <Stat key={metric.name} label={metricName(metric.name)} value={metric.value} />)}</div>{!metrics.length && <div className="card empty">指标加载中…</div>}</>; }

function Stat({ label, value }: { label: string; value: string }) { return <article className="card stat"><span>{label}</span><strong>{value}</strong></article>; }

const root = document.getElementById("root");
if (!root) throw new Error("root element missing");
createRoot(root).render(<App />);
