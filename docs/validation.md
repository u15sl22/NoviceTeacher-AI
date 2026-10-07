# Alpha validation — 2026-10-07

## Deployment/storage update — 2026-09-30

- Container PostgreSQL suite: **72 passed, 1 skipped**; production frontend build passed.
- Added multi-stage Dockerfile, app/PostgreSQL Compose, private database network, persistent document volume, migration and backup/restore tools.
- Storage archive round-trip, path traversal rejection, lossless history copy and non-empty target rejection passed locally.
- Additionally copied a read-only snapshot of the real SQLite database into a temporary SQLite target and compared every original column: **26 tables, 8 sessions, 41 dataset items, 1118 annotations** verified. This exercises copy logic with actual data, not PostgreSQL compatibility.
- Docker image build, Compose runtime, live PostgreSQL migration, health checks and isolated PostgreSQL tests passed. A real second-host restore rehearsal remains outstanding.
- Docker/PostgreSQL is now live; the SQLite snapshot was migrated with original identifiers and row verification. DOCX/text-PDF upload, private download, V0 linkage and the persistent document volume are implemented.
- Current upload and model-profile validation: container PostgreSQL **72 passed, 1 skipped**; production frontend build passed. Invalid DOCX, textless/scanned PDF, unsupported extensions, attachment reuse, source download, profile selection, snapshot freezing and server-side secret isolation are covered.
- Real DeepSeek/PostgreSQL smoke passed with `deepseek-flash`: **1 suggestion**, one context snapshot, decisions, completed round, termination and export. It ran in a disposable PostgreSQL schema; business sessions remained **8** and leftover smoke schemas remained **0**.

## Current alpha results

- Backend: **58 passed**, including user isolation, client-header impersonation prevention, legacy snapshot compatibility, contributor toggles, source verification/revocation, context budgets, safe replacements and migration constraints.
- Browser: **2 passed** in headless Microsoft Edge against an independent mock SQLite database on port 8002. Includes two rounds, refresh recovery, accept/reject, export, history reopen and mobile network retry.
- Production frontend build passed; bundle size advisory remains. Input/review/final/mobile screenshots saved under `.runtime/` and input layout inspected.
- Root SQLite migrated with original rows/columns preserved, foreign key check clean, CHECK constraints retained and Alembic schema check clean. Backup: `.runtime/pre_alpha_backup.db`.
- Imported **41 DatasetItem / 1118 raw DatasetAnnotation**, repeated import adds zero. **0 verified cases / 0 verified knowledge**; raw annotations are not eligible for retrieval.
- Real LLM: DeepSeek `deepseek-flash` passed the full smoke flow against live PostgreSQL on 2026-10-07. This verifies integration and one generated revision, not pedagogical quality.
- PostgreSQL: Docker Compose build, migrations, health check, isolated integration suite and real-model disposable-schema run passed.
- Two backend dependency deprecation warnings remain (Starlette/httpx and AnyIO); no failing test.

See [alpha.md](alpha.md) for the complete 30-item delivery report, operation commands and limitations.

---

## Historical MVP validation — 2026-09-16

## Verified on this machine

- Python 3.12.10; Node.js 22.17.0; Windows; SQLite local persistence.
- `python -m pytest backend/tests -q`: **39 passed**.
- `npm run build`: successful Vue/Element Plus production build.
- `npm run test:e2e`: **2 passed**, real headless Microsoft Edge against the running FastAPI server.
- Alembic `upgrade head` on a new SQLite database and repeat upgrade: passed.
- Alembic `check`: no schema changes detected.
- PostgreSQL offline migration SQL: JSONB and timezone-aware timestamps verified.
- Generated UI screenshots inspected for desktop input/review/final and mobile layout.

### Coverage

Session creation and duplicate submission; lossless heading parsing; single, empty-heading and 80-section inputs; 0/1/2 suggestion contracts and >2 rejection; both accepts and both rejects; chained section versions; V0–V5 snapshots; round completion/continuation/termination; sixth-round rejection; terminated-session mutation rejection; cross-session access validation; repeated and concurrent requests; complete recovery through a new app instance; event-sequence replay to reconstruct final content; exact rendered-version acknowledgement even after another view becomes stale; malformed/truncated/refused output; missing key, HTTP failure, timeout and network failure; persisted failure plus successful retry; saved configuration surviving provider changes.

The real HTTP adapter is exercised with a **mock HTTP transport** for DeepSeek and Zhipu URL/model combinations. This validates request construction and output handling, not vendor availability or actual answer quality.

Browser test 1 pastes a mathematics lesson, accepts/rejects, refreshes midway, advances sections, completes two rounds, terminates, refreshes final state, selects V0 and checks the full JSON export. Browser test 2 simulates a failed create request, refreshes, retries the retained submission key/payload and checks mobile overflow.

## Remaining external validation

1. **Teacher review of model quality**: the real DeepSeek chain is operational, but one successful revision does not establish correctness, grade appropriateness or pedagogical value.
2. **Second-host restore rehearsal**: backup/restore tooling is tested, but a complete rehearsal on the eventual server remains required.

## Non-blocking tooling notices

The installed Starlette/AnyIO test tooling reports two deprecation warnings. The production frontend builds with a bundle-size advisory because Element Plus is included as a full plugin. These do not fail tests; selective component imports can be adopted if deployment bandwidth becomes relevant.

## Demo handoff

打开 `http://127.0.0.1:8000` 进入 Docker/PostgreSQL 应用。新会话可选择任意已在服务器配置密钥的 profile；DeepSeek 是默认项，智谱在设置 `ZHIPU_API_KEY` 前保持禁用。旧会话继续使用创建时保存的 profile 配置。

No remote repository was created or pushed, and no release tag was created before real-service acceptance.
