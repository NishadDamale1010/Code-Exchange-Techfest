# Change Report

This document summarizes **all implemented fixes and UI/UX improvements** across the recent iterations.

## Backend (`main.py`)

1. Added request schemas via Pydantic:
   - `CodeRequest` for `/run` and `/submit`
   - `DraftRequest` for `/api/save-draft`
2. Added explicit validation helper `get_valid_test_case(...)`:
   - returns `404` when team has no assignment
   - returns `400` when submitted `problem_title` is invalid
3. Implemented missing admin endpoint:
   - `POST /api/admin/randomize-all/{tier}`
4. Added participant-safe hint endpoint:
   - `POST /api/hint/{tid}/{pid}` (applies penalty + returns hint)
5. Fixed team count consistency:
   - replaced hardcoded `1..10` with `1..TOTAL_TEAMS` in timer/assignment flows
6. Added duplicate scoring prevention on submit.
7. Updated leaderboard API to include **all teams**, including zero-score teams.
8. Added bounds checking on `/api/admin/set-team-count/{count}`.
9. Improved runtime execution flow:
   - language validation
   - temp file execution cleanup for Python/C++

## Participant IDE (`index.html`)

1. Major UI refresh:
   - structured topbar + status badges + card-based problem panel
   - improved visual hierarchy and readability
2. Better realtime UX:
   - connection badge (`Realtime Connected` / `Disconnected`)
   - cleaner console logs and phase transitions
3. Reliability improvements:
   - dynamic API host (`window.location.origin`, file-protocol fallback)
   - robust JSON request handling with surfaced errors
   - action button busy states to prevent accidental double actions
4. Draft lifecycle:
   - restore draft + language on load
   - periodic autosave retained
5. Hint workflow:
   - integrated with `/api/hint/{tid}/{pid}`
   - inline hint preview in the side panel

## Admin Panel (`admin.html`)

1. Added status banner for operational feedback.
2. Added safer request wrapper and error handling across all controls.
3. Connected `SHUFFLE EASY` to live backend endpoint.
4. Improved assignment dropdown labels (include problem tier).
5. Dynamic API host resolution for non-local deployments.

## Leaderboard (`leaderboard.html`)

1. Added update timestamp.
2. Added visual highlight for top 3 ranks.
3. Dynamic API host resolution.

## Notes

- The previous issue where only an audit was provided has been addressed by implementing concrete code changes.
- The system now has connected admin actions, safer run/submit handling, and improved UX across participant/admin/leaderboard screens.
