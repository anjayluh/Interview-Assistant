# Startup and local session-settings repair

## Confirmed causes

1. The actual Brave tab displayed a three-minute duration. Its real `POST /api/live-quote` returned HTTP 422: `options.minutes` must be at least 5. The frontend discarded non-200 response bodies, substituting a generic message suggesting invalid duration or a dead backend. The same running server accepted 60 minutes with HTTP 200 and no blockers. How the field acquired 3 is not recorded; the source default was already 60. Fresh-page startup now explicitly reapplies the 60-minute default instead of accepting a restored invalid value.
2. A real Desktop Stop/Start test exposed a separate launcher race. The old process closed its listening socket before releasing the process lock. Start's initial lock attempt failed; its subsequent loop only checked health and never attempted to acquire the now-free lock. It eventually exited with “Another assistant instance is starting or stopping,” leaving no backend. This was reproduced with the installed Python environment and actual Desktop command files.

## Corrections

- `app/static/app.js`: validate whole-number durations locally (5–120); display actual backend validation/HTTP errors; default a fresh page to 60; retry only local settings checks after temporary failures; use a five-second timeout; recheck on WebSocket connection/page activation. An expired local cookie is refreshed through the existing local root route, preserving backend authentication. Provider calls are never retried by these recovery checks.
- `app/static/index.html`: show the allowed range and a **Use 60 minutes** button.
- `scripts/launch.py`: retry acquiring the lock while a previous process exits; Stop waits for lock release before reporting completion; bound Uvicorn's graceful connection shutdown. Existing healthy instances remain reusable; no duplicate backend is deliberately started.
- `tests/test_launcher_restart.py`: cover lock release/reacquisition, existing-instance reuse and shutdown completion.
- `tests/test_session_settings.js`: cover invalid duration, valid correction, backend HTTP 422 detail, network outage and HTTP 503 recovery.

Session authorisation, UGX 0, model, audio settings, free-only policy, no automatic provider retries, deadlines and STOP ALL remain unchanged. Recovery checks call local endpoints only and cannot authorise a live session.

## Actual lifecycle verification

The first test reproduced the launcher failure described above. After correction, the app was bootstrapped and then two complete cycles ran the actual `~/Desktop/Stop Interview Assistant.command` and `~/Desktop/Start Interview Assistant.command` scripts.

| Cycle | Actual settings POST | Duration | Payment policy | Blockers | Settings ready after Start |
| --- | --- | ---: | ---: | --- | ---: |
| 1 | HTTP 200 | 60 min | UGX 0 | none | 1.31 s |
| 2 | HTTP 200 | 60 min | UGX 0 | none | 0.88 s |

Each Stop waited for the backend to finish, and each Start used the installed `.venv` and the normal launcher/browser-opening path. Verification tested the real settings request, not just `/health`. The app was left running.

Brave's original failing tab was observed directly with Connected locally, duration 3 and a disabled Start button. Browser automation subsequently encountered native-control interruptions/timeouts, and the isolated diagnostic browser could not acquire the control connection while another tab owned it. No authentication/ownership check was removed to make a diagnostic tab appear ready.

After the corrected restart cycles, a read-only check of the production app observed a **user-started running session with four transcript entries and one completed AI answer**. At that point further restart/connection tests stopped to avoid interrupting the user's interview. No agent-initiated live session or provider request was made; this was observation of the user's active application. Physical phone display and audio accuracy were not inspected in this check.

Automated verification: 130 Python tests passed, both JavaScript acceptance checks passed, JavaScript syntax passed, and `git diff --check` passed. Slow/unavailable local settings responses were simulated for recovery tests; real startup timings are shown above.
