# Signal frontend implementation plan

Goal: implement the user-approved summary/chart demo layout against the active backend contracts.
Architecture: React/TypeScript UI, typed Api interface, HTTP adapter and explicitly labeled in-memory demo adapter. Vite proxies /api to a configurable local backend; runtime bearer is held in memory only. Product browsing is paginated, analyses poll durable status, completed results alone expose chart/evidence. Source/batch are chosen before analysis. Old reports retain their scope. All code lives in frontend/; no shared Git operations.

Approved design: mockups/index.html. User requested implementation after iterative approval. Blue palette; simple two-view navigation; product-specific summary + chart; no separate issue list. Guidance appears on demand. Backend owns identity and real analysis; selecting views is not authorization.

Tasks:
1. Typed contracts and adapters. Test request paths, auth, idempotent submission and safe errors; implement HTTP transport and demo flow.
2. Product catalog and PM report. Test completed-only output and product isolation; implement paginated selection, source/batch/mode dialog, polling, error/empty states, summary/chart, evidence and guidance.
3. Reviewer form. Test validation, submission and independent processing statuses; implement with persistent-per-attempt idempotency key and retry.
4. Build, browser desktop/mobile checks, independent review and handoff. Verify actual backend contracts again; document live-service limitations.

Review focus: stale asynchronous response after product/session change; pending/failed jobs mistaken for completion; uncertain submission retry duplicates; role UI mistaken for auth; missing pagination or invented backend fields.

Rulings: no commits or worktrees because backend/data tasks share an unborn repository and explicit user scope is frontend/ only. API decisions route is still being implemented; coordinate its exact schema before final integration.

## Completion record — 2026-09-27

Tasks 1–4 complete. Verified current backend product/review/analysis/decision/question response models and actual route registration. 16 Vitest tests pass; TypeScript/Vite production build passes. Chrome browser demo flow passes at 1440px and 390px with zero script errors/overflow. Real FastAPI routing/serialization contract flow passes with injected deterministic in-memory services: 35-product pagination, analysis, evidence/full review, guidance, questions, reviewer save/status, server role denials. No live Mongo/LLM/Hindsight calls were made by these frontend tests.

Independent review fixes: native Escape cancellation while a save is busy; uncertain guidance writes reconciled instead of duplicated; review draft correction disabled during a retry so its idempotency key cannot be lost. Browser contract test also caught/fixed the native fetch receiver binding. Tests cover all these fixes.

Runtime: Vite at localhost5173, proxy to localhost8000; sample mode is default. No commits, deployment, backend edits, data edits or secret changes. Temporary contract fixture servers are stopped after validation.
