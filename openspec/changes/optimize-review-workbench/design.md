## Context

See `proposal.md` for motivation. The web client already has a two-column review layout and the API already reloads the PostgreSQL read snapshot before protected reads. The remaining persistence gap is that `TriageService.review` changes the domain entity status after saving the review decision but does not call the adapter persistence hook.

## Goals / Non-Goals

**Goals:**

- Keep the review queue and selected ticket details visible in a dense, usable work area.
- Persist the `needs_review` to `reviewed` transition through the repository adapter.
- Verify the complete reviewer-to-admin flow through the API.

**Non-Goals:**

- No new review fields, routes, permissions, or database migrations.
- No automatic polling of the administrator page; the existing refresh action remains the explicit read trigger.
- No redesign of unrelated pages.

## Decisions

- Add a dedicated `review-detail` class and constrain only review-page controls. This avoids changing the global form sizing used by classification and data entry.
- Keep the two-pane layout and make the detail panel internally scrollable with a viewport-relative maximum height. On narrow screens the existing responsive breakpoint switches it to one column.
- Persist the ticket immediately after its domain transition by calling the repository's optional `persist_ticket` hook. This is compatible with PostgreSQL and SQLite adapters and remains a no-op for in-memory repositories, which already hold the changed entity.
- Make the administrator list use the latest review decision's labels and primary queue when one exists, while retaining the model prediction as the pre-review fallback.
- Test through `TestClient` with `PostgresRepository` backed by a temporary SQLite database so the test exercises API refresh, review persistence, and the administrator list contract together.

## Risks / Trade-offs

- [Risk] A very long review history can still require scrolling → The detail panel gets its own bounded scroll area while keeping the decision controls in the same panel.
- [Risk] A reviewer may submit while another process changes the same ticket → Existing repository and review validation behavior is preserved; concurrency conflict handling is outside this focused fix.

## Migration Plan

1. Run backend and frontend checks locally.
2. Copy the changed source files to the VM deployment checkout.
3. Rebuild and recreate the API, Worker, and Web Compose services.
4. Log in as reviewer, submit a decision, then log in as admin and refresh 数据管理 to verify `已复核`.

## Open Questions

无。
