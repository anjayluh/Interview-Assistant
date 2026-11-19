# Micro1 regression investigation

## Evidence and limits

The user reports a successful Micro1 interview around November 1–3, followed by an interview that started and paired but produced no suggestions. The supplied desktop text was:

> Groq request failed. Check model access, credits and network before starting a new session. Live transcription and cloud answers are stopped; local practice, CV tools and phone pairing remain available.

This is an application-generated fallback, not Groq's response body. It contains no HTTP status, provider error code or request ID. It does not establish exhausted credits.

No saved interview sessions or transcript/answer archives were found in the project, its `.runtime` directory or the associated Codex project workspace. `Session.history` and `suggestions` are bounded in-memory collections; cleanup clears them. Provider logging is suppressed and Uvicorn access logging is off. `.runtime` contained only the backend lock and stop token before this correction. The running app's read-only state was idle with zero history entries, zero answers, no latest question and zero latency records. Reading that state did not start audio or call providers.

The repository and reflog begin with commit `8adaa27` on November 10, 2026. No earlier source backup was found in the associated project workspace. Thus the reported November 1–3 working interview cannot be assigned to a recovered session or Git revision. No microphone test, mock interview, resource benchmark or empty state has been substituted for it.

## Requested session comparison

| Item | Successful interview | Latest failed interview |
| --- | --- | --- |
| Identity/date | User-reported Micro1, approximately November 1–3; no saved record | User-reported failed Micro1; exact date/ID unavailable |
| Duration | Not recovered; cannot verify five minutes or longer | Not recovered |
| Actual interviewer questions | Reported by user; transcript unavailable | Transcript unavailable |
| Live transcription | User reports it worked | Session startup reported; actual transcript event record unavailable |
| Generated suggestions | User reports successful answers | User reports none |
| Phone | Historical delivery events unavailable | User reports successful pairing and persistent Waiting message |
| Role, selected CV and professional context | Not recovered | Not recovered |
| AssemblyAI configuration/events | Historical runtime settings/events unavailable | Historical runtime settings/events unavailable |
| Groq model, endpoint and request parameters | Historical request unavailable | Historical request unavailable |
| Prompt/context size | Not recorded | Not recorded |
| HTTP status/body/request ID | Not recorded | Not recorded; supplied UI text is generic fallback |

No actual session-to-session comparison beyond these user-reported observations is possible with the retained evidence.

## Versioned code comparison (not a claim about the November 1–3 session)

| Version | Observable source change |
| --- | --- |
| Nov 10 `8adaa27` | 871-character shared strategy; plain spoken output; output caps 450/650/900; 25-second Groq timeout and zero Groq SDK retries |
| Nov 11 `75213e2` | Personalised CV/profile context, JSON schema output, 3,086-character strategy; output caps 900/1300/2000 |
| Nov 16 `84755b9` | Whole-interview intent/context strategy, 6,129 characters; supporting fields and previous-suggestion context |
| Nov 19 `39106ab` | Free-only request bounds; explicit `service_tier=on_demand`; Groq failures changed from reporting generation error to full live shutdown |
| Nov 19 `c281b44` | Removed manual billing/balance gates; retained provider errors, no retries, consent and cleanup |
| Nov 19 `6b38332` | Local approved phone pairing and 60-minute default/120-minute maximum; phone still used 1.5-second state polling |
| Uncommitted speaker/latency change preceding this investigation | Added source metadata, partial push delivery, 250 ms question settling, clarification cancellation, JSON-object answer streaming and unsupported `stream_options` keyword |

Current configured model remains `openai/gpt-oss-20b`. The installed SDK's base URL is `https://api.groq.com`; its generation resource posts to `/openai/v1/chat/completions`. No model or endpoint switch is made by this correction. The 25-second SDK timeout and zero retries were already present in the earliest tracked version. Audio remains 16 kHz PCM16, 50 ms blocks, independent microphone/system streams, and 500/1400 ms turn silence. New source-aware question handling still triggers only final interviewer turns, with preparatory instructions/constraints attached; candidate speech does not automatically trigger requests.

Prompt growth and larger completion limits could affect token-based rate limits, context limits or response time, even after an account was unused for a week. They are risks to observe, not established causes here. No account-specific remaining allowance or historical 429 response was obtained. Groq documents request and token limits separately from billing; `on_demand` is its default tier, so that parameter alone is not evidence of an invalid request or paid fallback.

## Confirmed reproducible current regression

The pinned installed Groq SDK is 1.7.0. Its `AsyncCompletions.create` signature accepts `stream=True` but does not accept `stream_options`. The preceding live-streaming change passed:

```python
stream=True, stream_options={'include_usage': True}
```

Using the actual installed SDK with an offline `httpx.MockTransport` reproduced:

```text
TypeError: AsyncCompletions.create() got an unexpected keyword argument 'stream_options'
```

**Transport requests sent: 0. HTTP status: none.** This failure occurs during local Python argument binding, before Groq can accept, reject or bill a request. Permissive `AsyncMock` SDK stubs in the previous tests accepted arbitrary keywords and missed the incompatibility.

This is a confirmed cause of failure whenever the affected streaming implementation is used, and the strongest code-supported explanation for a failure on that version. It cannot be represented as the recovered exact error from the reported interview: no historical exception/response was retained, and its exact loaded revision is unrecorded.

## Notification and event chain

For the reproduced defect the chain reaches transcription/question detection and answer generation, then stops at **SDK request argument binding** before any HTTP request. It cannot be diagnosed as free-credit exhaustion.

Two phone presentation defects independently hid useful status:

1. The push client handled transcript/answer/error events but ignored `groq: generating`, so it could continue to display Waiting while a generation attempt was in progress.
2. An error could be displayed and then immediately overwritten by the cleanup `clear` event. A reconnect snapshot contained no retained failure state. The earlier polling implementation did not expose provider failure details at all.

The desktop WebSocket handler displays errors when received; no code waits for the laptop window to regain focus. Without event timestamps from the interview, an actual delay until return-to-laptop cannot be established. The user may have first seen an already-displayed error then. The phone's missing/overwritten status is directly reproducible.

## Minimal correction

- Removed only unsupported `stream_options`; retained `stream=True`, the same model, endpoint, strategy, temperature, output bounds, timeout and service tier. Existing stream usage handling reads the provider/SDK's available usage fields without requesting an unsupported option.
- Added explicit error classification for 400, 401, 402, 403, 404, 429, 5xx, network, timeout and local application/validation errors. A 429 is labelled rate-limit/quota rejection with free balance unknown, not exhausted credits by assumption.
- Phone immediately handles generating/error events. Failed answer status and safe metadata survive cleanup and reconnect snapshots. Details are visible in the existing diagnostics panel on both devices.
- Non-access/billing/rate-limit generation failures pause Groq without retrying it while independent transcription continues until STOP ALL/deadline. Subsequent interviewer turns are transcribed but cannot trigger further Groq attempts in that session. Authentication, access, payment-required and rate-limit failures retain full live-service shutdown. AssemblyAI failures still terminate live capture; offline practice/CV/phone pairing remain available.
- The production entry point saves only the latest safe Groq failure to owner-only `.runtime/last-groq-error.json`, using an atomic write. It records UTC time, category, status, exception type, allowlisted error code/parameter/request ID, model, endpoint, format, byte counts and request settings. It does not save API keys, Authorization, raw provider bodies/messages, prompts, CV contents or transcripts. This is not an interview history and cannot recover earlier failures. Factory-created test apps do not write production diagnostics unless an explicit test path is supplied.

UGX 0, no paid fallback, no model substitution, no paid subscriptions, no automatic provider retries, deadlines, STOP ALL, cleanup and local-only functionality remain. No audio rewrite was made.

## Verification and remaining uncertainty

Automated tests use mocked transports/providers only. A new acceptance test invokes the installed SDK against offline SSE HTTP responses, proving actual keyword compatibility and wire request shape. Tests cover final interviewer triggers, immediate generating/error events, completed answer delivery, cleanup/reconnect retention, independent generation pause, status classification, secret exclusion, owner-only saved diagnostics, and exactly one attempt per failed request. Existing billing/deadline/audio-cleanup tests remain required. The simulated-DOM test covers phone status changes and verifies an error is not reset to Waiting.

Commands: `.venv/bin/python -m pytest -q`, `node tests/test_phone_render.js`, JavaScript syntax checks and `git diff --check`.

No real Groq/AssemblyAI requests or paid tests were made. No physical iPhone or real Micro1 interview was run for this correction. Restoration of the reported November 1–3 outcome is therefore not yet live-verified. Restart the Mac backend and re-pair/reload the phone to load the correction. If a subsequent authorised free session fails, its exact safe classification should now be available in the diagnostics panel and last-error file rather than the generic message.

Sources: [Groq API errors](https://console.groq.com/docs/errors), [rate limits](https://console.groq.com/docs/rate-limits), [service-tier defaults](https://console.groq.com/docs/service-tiers). Local SDK signature/source and offline transport reproduction provide the evidence for the TypeError.
