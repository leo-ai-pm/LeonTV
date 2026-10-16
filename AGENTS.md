# Leon TV repository guide

- Product: local AI video workspace evolving toward an Agent Harness.
- Application root: `Leon TV/Leon TV/` from this Git repository root.
- Backend: FastAPI/Pydantic/Python; frontend: Next.js/React/TypeScript/React Flow.
- Current execution: fixed six-stage workflow with stage handlers and some local review loops.
- `experiments/harness_poc` is deterministic; `experiments/tudou_ab` is a controlled trial. Neither establishes a full autonomous product Harness.
- Product architecture/status: `docs/ARCHITECTURE.md`; priorities: `docs/ROADMAP.md`.
- Setup: `docs/GETTING_STARTED.md`; checks: `CONTRIBUTING.md`.
- Backend start: from `Leon TV/Leon TV/backend`, run `uv run python api_server.py` (127.0.0.1:8000).
- Frontend start: from `Leon TV/Leon TV/frontend`, run `npm run dev` (localhost:3000).
- Backend checks: `uv run --extra dev python -m pytest -q`.
- Frontend checks: `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`.
- Never print or commit real configuration, credentials, private scripts, signed media URLs or runtime receipts.
- Keep `backend/code/` and `backend/harness_data/` local; publish selected demo copies only under `docs/assets/demos/`.
- Tests must use synthetic authorization and credentials, not private production state.
- Preserve user assets and existing work; inspect changes before editing or committing.
- Preserve upstream copyright/license and compatibility identifiers needed for old data.
- Report code, mocked checks, real model execution, content quality and human acceptance separately.
- Do not restart shared services, generate paid media or publish changes without task authorization.
