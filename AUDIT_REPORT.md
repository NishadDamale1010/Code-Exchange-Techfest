# Codebase Deep Review & Betterment Suggestions

## Overview
This project is a FastAPI + WebSocket event coding platform with:
- `main.py` as backend/API + execution engine
- `index.html` as participant IDE
- `admin.html` as mission control
- `leaderboard.html` as display board
- `rounds/pool.json` as problem bank

## High-Impact Issues (Fix first)

1. **Broken Admin action (`SHUFFLE EASY`)**
   - UI calls `POST /api/admin/randomize-all/{tier}`.
   - Backend has no matching endpoint, so this button always fails.
   - **Impact:** core event control workflow is broken.
   - **Fix:** either implement backend endpoint, or remove/disable the button.

2. **Possible server crashes in `/run` and `/submit`**
   - Code assumes assigned problem exists and that `problem_title` key exists in `test_cases`.
   - Missing assignment / mismatched title can trigger `NoneType` access errors.
   - **Impact:** 500 errors during contest.
   - **Fix:** add input validation + explicit `4xx` responses.

3. **Unsafe code execution model**
   - User code runs directly via local `python -c` and local binaries.
   - No sandbox, no syscall restrictions, no filesystem isolation.
   - **Impact:** security risk if exposed beyond trusted LAN.
   - **Fix:** run in isolated container/sandbox with strict resource limits and blocked network/filesystem.

4. **Team count not respected consistently**
   - `TOTAL_TEAMS` is configurable, but `/api/admin/start-all` still hardcodes `1..10`.
   - **Impact:** newly configured teams won't get timer starts.
   - **Fix:** iterate using `TOTAL_TEAMS` everywhere.

## Medium-Priority Correctness Gaps

5. **No duplicate-submission guard**
   - Multiple successful submissions can repeatedly add points.
   - **Fix:** enforce one accepted submission per `(team_id, problem_title)` or implement attempt policy.

6. **Hint penalty endpoint exposed to players**
   - Player UI directly calls admin penalty API.
   - **Fix:** add authenticated `request-hint` endpoint to apply penalty + return hint atomically.

7. **Draft lifecycle incomplete in UI**
   - Frontend auto-saves drafts but never loads saved drafts on startup.
   - **Fix:** call `/api/get-draft/{tid}/{pid}` during initialization and restore editor/language.

8. **Leaderboard omits zero-score teams**
   - Query only includes teams present in `scores` table.
   - **Fix:** synthesize all teams from `1..TOTAL_TEAMS` and left-join score totals.

9. **Hardcoded API host in HTML files**
   - `http://localhost:8000` prevents easy deployment behind hostnames/reverse proxies.
   - **Fix:** derive from `window.location.origin` or config variable.

## Reliability & Operability Improvements

10. **Race/cleanup concerns for timer tasks**
    - Cancelled task sends `STOP_TIMER`, but old tasks may overlap if fast retries happen.
    - **Fix:** centralize timer state transitions and ensure task replacement is atomic.

11. **SQLite access pattern**
    - Frequent open/close for every request; okay for small loads but can lock under stress.
    - **Fix:** use a small DB helper/context manager and WAL mode.

12. **Input validation absent**
    - `tid`, `pid`, payload fields are mostly unchecked.
    - **Fix:** use Pydantic request models with strict schemas and validation constraints.

13. **Error handling is too silent**
    - Several frontend `catch(e){}` blocks swallow useful diagnostics.
    - **Fix:** log to console/onscreen telemetry for admin troubleshooting.

## UX & Product Betterments

14. **Phase state can be bypassed**
    - Client controls `problem_title` sent to run/submit endpoint.
    - **Fix:** backend should infer current valid phase/title from team state, not client text.

15. **No clear contest lifecycle guardrails**
    - Actions like assign/start/reset are callable anytime.
    - **Fix:** add global contest states (`idle`, `running`, `swapped`, `ended`) and enforce transitions.

16. **No authentication/authorization**
    - Admin routes are public.
    - **Fix:** minimal API key/JWT for admin operations; separate participant/admin permissions.

## Suggested Implementation Order

1. Fix missing admin endpoint mismatch.
2. Add validation/guard clauses for `/run` and `/submit`.
3. Prevent duplicate scoring.
4. Unify `TOTAL_TEAMS` usage.
5. Add draft restore and better error messages.
6. Add basic auth for admin routes.
7. Move execution to sandboxed workers.

## Quick Wins (1-2 hours)

- Disable or fix `SHUFFLE EASY` button.
- Return `404` if no assignment for team.
- Return `400` for unknown `problem_title`.
- Use `TOTAL_TEAMS` in all loops.
- Load draft on IDE startup.
- Show API errors in admin/IDE console.
