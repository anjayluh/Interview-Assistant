# Live speaker separation, response delivery and latency

## What was wrong

The phone rendered `latest` (raw interviewer speech) inside its Suggested answers section, and its state endpoint omitted the candidate transcript. The two AssemblyAI connections were already created with independent source callbacks: `interviewer` for the selected system-audio input and `me` for the selected microphone. Successful transcription never established physical speaker identity; device selection and acoustic bleed remain separate concerns.

The old phone waited up to 1.5 seconds for each poll and Groq generation returned only a completed JSON object. The phone now has independent INTERVIEWER, YOU and SUGGESTED ANSWER sections. AI cards contain generated spoken text; raw transcript excerpts are not used as answer content.

## Data routing and turn handling

- Every transcript retains its original role, physical source label, SDK turn key, partial/final status, receipt time and original final text. Device selection still determines which physical audio reaches each stream.
- Partials are delivered immediately and incrementally update their source row. They never independently trigger generation. Formatted repeats update the existing row without starting a new request.
- A completed interviewer question or coding request starts a 250 ms settling interval. Candidate responses and candidate questions never trigger automatic generation. Preparatory instructions are attached to the next question. Subsequent coding examples, constraints and follow-ups remain attached to the challenge.
- A clarification cancels pending/obsolete generation, closes its stream and creates a new revision after settling. Cancelled requests can already have consumed free capacity. This is not a refund, paid fallback or retry of a failed request. No provider failure is automatically retried.
- Model input retains the selected interview/CV context, recent source-labelled conversation and relevant previous suggestions. Duplicate microphone echoes are excluded from model conversation context.
- Matching speech of at least six normalised words, at least 91% text similarity and within three seconds across the two sources is marked `duplicate_of`. The microphone copy is hidden rather than relabelled as candidate speech. If the microphone arrives first it is removed when the matching system transcript arrives. Original source records stay available in authenticated session state.

This duplicate rule is a heuristic, not diarization, proof of who spoke, or acoustic echo cancellation. It can miss delayed/differently transcribed echoes or hide a genuine rapid repetition. A real microphone/speaker test is still required. Headphones reduce the physical source of speaker bleed.

## Streaming without changing the answer strategy

Groq's documented strict `json_schema` structured-output mode does not support streaming. For live streaming the app deliberately uses `json_object` on the same configured model, with the complete existing answer strategy and required schema in the prompt. It requests spoken `text` first and incrementally decodes only that top-level JSON string, never code, metadata, raw JSON syntax or reasoning tokens. Supporting material remains below the spoken response.

Draft text is explicitly labelled as still generating and awaiting validation. Before completion is accepted, the app verifies all required fields/types, categories and experience labels, then applies the existing verified-example filter and size limits. Malformed output or provider errors clear drafts and pause Groq without a retry. Non-access/billing/rate-limit errors allow independent transcription to continue until STOP ALL/deadline; authentication, access, billing and rate-limit errors retain full live shutdown. Server-enforced schema conformity is therefore replaced by prompt guidance plus application validation for the streaming path; partial text has not passed complete-response validation. A non-streaming call still uses its existing strict schema where supported. No model substitution or automatic request-format fallback occurs if a configured model rejects streaming.

The same Groq token/request bounds, no-retry clients, AssemblyAI two-stream limits, session authorisation, 60-minute default/120-minute maximum, UGX 0 policy, deadline and STOP ALL cleanup remain. Provider free allowances may run out before the selected duration.

Official references: [Groq structured-output limitations](https://console.groq.com/docs/structured-outputs), [Groq streaming chat](https://console.groq.com/docs/text-chat).

## Mac-to-phone delivery

The paired phone opens an authenticated same-origin NDJSON response with Fetch streaming over the private-LAN listener. It receives a current snapshot, source transcript updates, generated spoken-text updates, completed suggestions and latency events. There is no browser secure-context dependency for this ordinary HTTP Fetch stream.

The phone receives no provider key and cannot authorise/start audio. Existing subnet, Host, Origin, pairing approval, credential expiry and revocation checks remain. Each stream rechecks credentials before delivery. At most three concurrent phone update connections are allowed. A bounded 64-event queue replaces backlog with a current snapshot if a phone is slow, keeping audio capture independent. Phone disconnect removes its subscriber; reconnect does not mutate the Mac interview context. Only local companion connections reconnect automatically. The laptop's existing controlling-WebSocket disconnect still stops live capture.

## What is measured

Both UIs show stage times relative to backend session start: first captured audio for each source, first transcript fragment per turn, finalised interviewer question, generation start, first generated content token, first decoded spoken-answer text and completed answer. First content-token timing excludes hidden model reasoning before content. Capture times come from the audio callback and are reported when the consumer first sends audio. They are session-source start times, not voice-activity or word-level acoustic alignment.

Phone diagnostics additionally show connection-to-first-snapshot elapsed time and received-to-next-animation-frame DOM update time, with a phone-local timestamp. That frame measurement approximates presentation timing; it does not prove physical pixels have painted. The app does not subtract unsynchronised Mac and phone clocks and claim a one-way Wi-Fi latency.

Audio inspection found 50 ms callback blocks and 50 ms 16 kHz mono PCM16 streaming chunks, an eight-block bounded capture queue, an eight-frame provider backlog guard, and streaming resampling off the capture callback. These remain unchanged. AssemblyAI min/max turn silence remains 500/1400 ms. No audio-quality reduction or unmeasured silence-setting adjustment was made. Partial display and push delivery remove avoidable UI waiting; provider and acoustic latency still need real measurement.

## Reproducible local measurement (not a live-provider benchmark)

A real TCP connection to this Mac's private Wi-Fi address used simulated capture/transcript callbacks and a mocked Groq stream. Mac and receiving test client ran on the same machine, so timestamp differences here use a shared clock. No actual iPhone, microphone signal, AssemblyAI request or Groq request was used.

One measured run:

| Interval | Measured time |
| --- | ---: |
| Last final question/instruction to generation start | 252.3 ms |
| Generation start to first simulated content token | 0.3 ms |
| Generation start to complete simulated response | 6.0 ms |
| Last final question/instruction to complete response | 258.3 ms |
| Server emission to TCP receipt, median | 1.0 ms |
| Server emission to TCP receipt, maximum | 2.4 ms |

That run received 21 events and made one mocked provider request after coalescing the question and constraint. The 6 ms result reflects the deliberately fast test fixture, not Groq speed. These numbers establish local routing/push overhead only. Actual transcription latency, Groq first-token latency, physical iPhone Safari rendering and acoustic bleed have not been measured.

## Verification

Run `.venv/bin/python -m pytest -q` for mocked provider and backend acceptance tests, and `node tests/test_phone_render.js` for the simulated-DOM phone data-flow check. The latter is not a real browser test.

Acceptance coverage includes source-specific callbacks, partial-before-final display data, no automatic generation from candidate speech/questions, instruction/challenge preservation, follow-up context, duplicate detection in both arrival orders, streamed spoken drafts before completion, malformed/failed stream cleanup, no failed-request retry, revision cancellation, phone stream revocation and reconnect isolation. Existing STOP ALL, deadlines, zero-payment, provider failure, local practice and pairing tests remain required.

Physical-device verification remains outstanding: test Safari on an actual paired iPhone, real microphone/system audio (including speaker bleed versus headphones), real provider latencies within available free allowances, and phone sleep/network reconnection. Do not describe mocked or same-Mac measurements as a verified live interview fix.


## SDK regression correction

See [REGRESSION-INVESTIGATION.md](REGRESSION-INVESTIGATION.md). The installed SDK rejected the streaming change's `stream_options` keyword before HTTP dispatch; it has been removed. New tests use the installed SDK with an offline HTTP transport. Phone generation/failure status now survives cleanup and reconnect. Safe failure details are retained without recording interview content. Earlier same-Mac streaming measurements used permissive SDK stubs and did not prove SDK wire compatibility.


## 2026-11-19: Mid-session JSON validation failure

The saved owner-only diagnostic identified `APIError`, no HTTP status, and provider code
`json_validate_failed` with `json_object` streaming on `openai/gpt-oss-20b`. An offline
HTTP/SSE reproduction through the installed Groq SDK produces the same classification.
The previous application-error message was misleading: this was a provider output-validation
failure, and the session handler incorrectly disabled all remaining suggestions.

GPT-OSS now uses the existing strict JSON schema without streaming, including when the
session supplies a draft callback. Answers appear complete after validation; the earlier
streamed-draft behaviour described above applies only to the generic non-strict adapter.
The model, provider, token caps, request timeout, zero SDK retries, free-only policy and
session deadlines are unchanged. Strict outputs do not support streaming according to
[Groq documentation](https://console.groq.com/docs/structured-outputs).

A JSON/output-validation failure clears only the current draft, records safe diagnostics,
and leaves suggestions available for a later distinct question. It does not retry the failed
question, replay it on reconnect, issue a repair request, or change provider/model. Other
application/network failures retain their existing pause behaviour. Access, billing and
rate/quota rejections retain the existing shutdown controls. STOP ALL and deadline cleanup
still cancel pending work and close both audio streams.

Tests use offline transports and mocked audio/STT only. Simulated clocks exercise 15-, 60-,
and 120-minute configured deadlines, inject a failed answer at minute seven, then verify
later answers and deadline shutdown. This is not a real-duration soak test or physical
iPhone verification. External provider quotas, network outages, access restrictions and
response limits can still interrupt service; the configured duration cannot guarantee
provider availability or free allowance for the entire interview.


## 25 November 2026: Strict JSON also failed in the real sample

The saved diagnostic reported HTTP 400 `json_validate_failed` with `json_schema`,
`stream: false`, `openai/gpt-oss-20b` and 1300 completion tokens. Strict mode did
not prevent the real failure. The exact provider-side reason (e.g. token exhaustion
versus decoding/validation) cannot be recovered from the allowlisted saved record;
there is no evidence here of credit exhaustion. The current local state reported
STOP ALL, which clears transcripts by design, so its empty history does not establish
that live transcription failed.

For live GPT-OSS answers the initial request now uses `response_format: text` and
`include_reasoning: false`, without streaming. The existing card is built locally;
fenced coding implementations are retained in the code panel. Career-evidence rules
remain in the prompt. No fabricated category/experience/source labels are supplied.
Structured output remains for non-live callers and the generic JSON adapter remains
tested; neither is an automatic fallback for a failed live request. Same model, one
request per question, unchanged token caps, zero retries and unchanged UGX 0 policy.
This supersedes the strict-JSON live path described above.

Offline installed-SDK tests cover ordinary speech, fenced code, no reasoning exposure,
no JSON/schema requirement in live requests, transcript/previous-answer retention,
subsequent questions after failure, 15/60/120-minute simulated deadlines and STOP ALL.
No real provider request, physical iPhone test or live-duration soak was performed.
References: https://console.groq.com/docs/api-reference and
https://console.groq.com/docs/reasoning (GPT-OSS uses include_reasoning, not reasoning_format).


## 25 November: Quality and review upgrade

See README sections on answer quality, full review archives and optional local audio.
This upgrade replaces reliance on the rolling UI buffers for future performance reviews.
It adds immutable personal-evidence slots plus a conservative local claim guard, distinct
question/clarification/check-in/goodbye routing, visible current targets and manual per-turn
requests. Prior generated answers are excluded from factual context. Source-labelled raw
transcripts and suspected echo decisions remain reviewable. Full session records link
answer revisions, turn keys, evidence banks, configured duration, SDK/model versions,
source-device configuration, token/finish metadata, routing outcomes, grounding removals,
connections and browser receipt coverage. Archive exports are Mac-only and make no cloud
calls. Local audio is optional/off until explicitly selected and confirmed.

Automated tests reproduce the retained Reveloop failures: unsupported two-million-record,
NGINX, Stripe/PayPal and invented-metric claims; new non-payment questions; design-pattern
check-ins; long echo fragments; full archives beyond 120 turns/12 answers/80 metrics;
protected downloads/deletion; receipt replay/absence; opt-in WAV recording, recording
backpressure and STOP ALL cleanup. Existing free-only, zero retry, duration and phone
security checks remain required. Mocked tests and desktop UI checks do not establish
live Groq answer quality or physical iPhone timing/speaker separation. Legacy sessions
remain labelled incomplete; no old missing events or vocal delivery are invented.
