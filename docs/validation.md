# MVP validation — 2026-09-16

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

## Not yet externally verified

1. **Real DeepSeek response**: no LLM_API_KEY was provided. `scripts/smoke_llm.py` correctly reports NOT RUN. Fill the root `.env`, run this probe, then complete a new Generic AI session before the professor demo. The visible server currently runs explicitly labelled Mock mode.
2. **Live PostgreSQL**: no PostgreSQL or Docker service was available on this machine. Run the provided migration and tests using `TEST_DATABASE_URL` on a dedicated PostgreSQL test instance. Offline DDL validation is not equivalent to live concurrency testing.

## Non-blocking tooling notices

The installed Starlette/AnyIO test tooling reports two deprecation warnings. The production frontend builds with a bundle-size advisory because Element Plus is included as a full plugin. These do not fail tests; selective component imports can be adopted if deployment bandwidth becomes relevant.

## Demo handoff

Open `http://127.0.0.1:8000` for the currently running Mock preview. To start a separate real-model demo while that preview remains open, configure `.env` and run `scripts/start.ps1 -Port 8001`. New sessions use DeepSeek defaults; old sessions keep their saved provider configuration.

No remote repository was created or pushed, and no release tag was created before real-service acceptance.
