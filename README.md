# Interview Assistant for macOS

A local, compact interview companion for an Apple Silicon Mac with 8 GB RAM. It captures your microphone and independently routed system audio, sends each to AssemblyAI Streaming v3, and asks Groq for suggested spoken answers to completed interviewer questions. All AI runs in the cloud. No audio recordings, database, Docker, Electron, local models or frontend build system.

## Quick start

1. Double-click `~/Desktop/Start Interview Assistant.command`. Brave opens at http://127.0.0.1:8765 in **LOCAL-ONLY** mode.
2. Manage CVs, edit the professional profile and prepare an interview. These operations require no keys and make no provider requests.
3. For audio routing, select the microphone and system input and run their separate **Test** buttons. Each ten-second test stays on your Mac.
4. Keep the cap at **UGX 0**. Choose the duration under **Live session safety**, select the two audio inputs and click **Start free live session**.
5. **STOP ALL** ends the active capture and live requests, clears the temporary interview data and returns to local-only mode. The Desktop **Stop Interview Assistant.command** exits the backend. Restart the backend after editing `.env`.

## API configuration

Create keys in your own [AssemblyAI dashboard](https://www.assemblyai.com/dashboard) and [Groq console](https://console.groq.com/keys). Check account credits, model permissions and spend limits there.

`.env` supports:

```
ASSEMBLYAI_API_KEY=
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-20b
MAX_SESSION_MINUTES=120
```

`.env` is excluded from Git and created with owner-only read/write permissions (0600). Browser code receives only configured/not-configured flags. SDK errors are replaced with safe messages. Never enable HTTP/SDK debug logs when using real keys. The backend remains loopback-only and rejects other origins/hosts. It uses an HttpOnly, SameSite cookie for browser control and a separate local stop token. One tab owns a session at a time.

The app checks whether your Groq account can use the selected model only after live-session authorisation. Groq charges and allowances are separate from the transcription estimate. Check [Groq's model and pricing information](https://console.groq.com/docs/models) and your own dashboard.

## Audio routing — actions you perform

BlackHole is already installed; do not reinstall it. The development environment saw the driver but could not enumerate any Core Audio devices. Installation presence does not establish working capture.

To hear the interviewer while capturing system audio:

1. Open **Audio MIDI Setup**, then **Window → Show Audio Devices**.
2. Inspect existing Multi-Output Devices first. Reuse one only if it includes your intended listening output and **BlackHole 2ch**. Otherwise use **+ → Create Multi-Output Device** yourself.
3. Enable your listening output and BlackHole 2ch. Use a consistent sample rate, typically 48,000 Hz. Choose the physical output as clock source where available and enable drift correction on the other device where supported. Wired headphones simplify feedback and Bluetooth sample-rate problems.
4. In the interview application's speaker setting, choose that Multi-Output Device. If it follows the macOS default, select the Multi-Output Device as the system output yourself. This routes all system sound, including notifications, into the capture; mute unwanted notifications yourself if needed.
5. Keep your meeting application's microphone set to your actual physical microphone. **Do not set its microphone to BlackHole**: doing so can send the interviewer's own audio back to them.
6. In Interview Assistant, choose your physical microphone under **Your microphone**, and **BlackHole 2ch** under **Interviewer · system audio**.
7. Test microphone: speak, verify its meter moves. Test system audio: play ordinary speech in Brave, verify the second meter moves and you can still hear it. Do these before starting a free live session.

No app setting changes audio routing for you. A Multi-Output Device sends playback to two outputs; it is not an input selector. The assistant opens the microphone and BlackHole independently, so an Aggregate Device is unnecessary. BlackHole's two stereo channels are downmixed to one interviewer source. This supports interviewer-versus-you attribution, not individual identification of multiple people sharing the system source. Physical speaker sound can bleed into the mic: headphones help.

[Official BlackHole routing instructions](https://existential.audio/blackhole/support/).

macOS may ask for microphone access for Terminal/Python (or the editor if launched there). Decide and approve the permission yourself under **System Settings → Privacy & Security → Microphone**, then restart the application. The page does not use browser microphone access. This implementation uses BlackHole rather than native screen capture, so it does not request screen recording access.

## Interface and behavior

**Interview setup** lets you pick a CV, job title, company, description and notes before starting. The selected CV defaults to the version you saved as default. **Master profile only** uses the broader profile alone. Each interview snapshots these choices on Start. **Standard** is the default answer length, with Brief and Detailed also available.

The transcript has separate interviewer and microphone panes. Interim text updates in place. Nearby segments from the same source are visually grouped where appropriate, while completed questions keep separate entries. The app retains the first completed provider text in memory and uses later provider formatting for display. **Suggest answer** handles a completed interviewer turn missed by the question detector.

Each answer shows the spoken response, category and experience label. A personal example appears when it matches a fact supplied for that question. Code and technical detail are expandable. Groq classifies and answers in one request. The prompt asks it to distinguish verified work, working knowledge and proposed approaches, and to preserve contribution wording. Review its suggestions for accuracy.

**Clear transcript** removes the current in-memory transcript and suggestions and cancels pending generation. Stopping a session or losing the browser connection clears temporary interview data. Audio and transcripts are never stored on disk.

## Professional profile and CV versions

Open **Professional profile & CVs** to edit the master profile. It is seeded with your verified employment, skills and outcomes. The displayed experience claim uses completed years from your verified career start month. Overlapping jobs stay in the employment history and do not add extra time.

1. Select **New CV version** or an existing version.
2. Choose a PDF or DOCX and click **Extract preview**. Review and correct the extracted text. Remove contact details and confirm that claims reflect your own contribution.
3. Review the suggested differences against the master and prior CV, then click **Save CV version**. The comparison uses similar text and numbers to flag possible changes, so check dates and figures yourself.
4. Rename, replace, delete or set the version as default from the same editor. Apply any verified changes to the master in its separate editor.

The app parses files locally and keeps only the reviewed text. It supports text-based PDFs (up to 20 pages), DOCX files, 5 MiB per upload and 30 saved versions. Scanned PDFs need a text layer. The owner-only store is `.runtime/professional-profile.json` and is excluded from Git. Groq receives a few relevant reviewed excerpts and role context per question. AssemblyAI receives audio and, if enabled, a fixed list of engineering terms.

## Architecture and resource limits

```
Mic RawInputStream → 8-frame queue → mono PCM16/resample → AssemblyAI v3 (me)
BlackHole input   → 8-frame queue → mono PCM16/resample → AssemblyAI v3 (interviewer)
Final interviewer turn → question/dedup gate → bounded context → Groq
FastAPI ↔ one browser WebSocket → transcript, meters, suggested answers
```

One Uvicorn process/worker, no reload watcher, and no separate background daemon. Audio callbacks copy bytes with nonblocking queue insertion only; network calls and conversion happen outside callbacks. Frames target 50 ms. Hardware audio calls and blocking queue waits use a small, on-demand Python thread pool. Each source retains at most eight native input frames (about 400 ms) and eight outgoing frames (about 400 ms), plus a small conversion tail. Overflow or a stalled device stops the session instead of building delay indefinitely.

Streaming uses the official SDK's async client and one connection per source. Current SDKs also offer client-side mixed-channel attribution; this app deliberately keeps the separate connections you requested, avoiding ambiguity during overlapping speech. The pinned SDK has an unbounded internal writer queue; `app/transcription.py` is its sole producer and checks that queue before every send, refusing to enqueue a ninth frame. This explicitly guarded private SDK access is covered by a real-SDK/fake-network protocol test; keep the pinned version unless you rerun the checks.

The transcript holds 120 completed turns, up to 5,000 characters each; up to eight short recent transcript excerpts, two relevant prior suggestions and bounded career and role excerpts reach Groq. The UI holds at most 122 transcript entries and 12 answers. Question tracking is bounded. UI event queues are capped at 128; a stalled UI is disconnected rather than accumulating updates. Level updates are throttled to roughly six per second per source. No transcript polling; only a local one-second duration display while running.

`sounddevice` supplies capture; NumPy and the small native `soxr` library provide safe stereo downmixing and stateful resampling from 44.1/48 kHz to 16 kHz. They perform signal conversion only, not AI inference. The resampler avoids audioop, which is unavailable in Python 3.13. `.venv` isolates all installed dependencies. `requirements.txt` lists direct runtime packages; `requirements-dev.txt` adds tests; `requirements.lock.txt` records the complete tested installation.

## Free live sessions

The user has confirmed that neither AssemblyAI nor Groq has a payment card and that no paid subscription, upgrade, auto-reload or invoice agreement is authorised. The app uses available AssemblyAI trial credit and Groq Free capacity with maximum user expenditure set to **UGX 0**.

Select the microphone and system input, choose the session duration and click **Start free live session**. Confirm the session when prompted. No exchange rate, balance reservation, 15-minute billing confirmation, pricing proof or quota entry is required. The app does not claim to know the remaining account allowance or promise that it will cover the selected duration. Authentication, access, billing and rate-limit failures stop live services. Other answer-generation failures pause suggestions while independent transcription continues until STOP ALL/deadline. Local practice, CV tools and phone pairing remain available.

The session defaults to **60 minutes**, selectable from **5 to 120 minutes**. The server enforces the selected deadline from before the first provider request, including connection setup. `MAX_SESSION_MINUTES` remains the optional server-side ceiling (120 by default). The local session-settings check prevents invalid durations and any non-zero spending policy. It does not contact a provider.

The previous three-answer cutoff and token/credit reservations have been removed. Request-size limits, bounded context, completion-token limits and duplicate suppression remain. AssemblyAI and Groq determine available free capacity, supported models and organisation rate limits. A free-credit, quota, rate-limit, authentication or access error stops the session and reports the provider failure. There are no automatic provider retries, paid fallbacks or automatic account changes.

**STOP ALL**, the deadline, provider failure, browser disconnection, missing laptop heartbeats for 15 seconds and graceful backend shutdown close both audio inputs and provider connections, cancel pending answer generation where supported and clear temporary interview data. Reopening a page does not resume a live session.

CV management, profiles, interview setup, local practice, OCR and audio tests remain local and available independently of live services. No API keys are exposed to the browser or logs, and audio is never written to disk.

Provider limitations remain. Trial credits can run out, free quotas reset on provider schedules and rate limits may interrupt an interview. The API key alone does not expose account billing status or remaining balance. The app relies on the user's confirmed free-account setup and provider enforcement, and cannot control external account changes or provider billing systems. It stops when a provider reports a failure and never authorises a paid upgrade.

## Installation/recovery

The project lives at `~/Interview Assistant`. To recreate its environment with Python 3.13 ARM64:

```sh
cd "$HOME/Interview Assistant"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
# Only if .env is missing:
cp .env.example .env
chmod 600 .env
.venv/bin/python scripts/launch.py
```

The launcher activates the virtual environment, locks out duplicate backend starts, opens Brave after startup and keeps errors visible. A duplicate start reuses the running backend. It does not install a login item or start at boot. The authenticated stop launcher requests graceful shutdown of this application only; it does not search for or terminate other processes.

## Troubleshooting

- **No devices:** close the backend; start it from its Desktop launcher outside the development environment. Check microphone permission for its launching application, hardware connection and Audio MIDI Setup. Click Refresh devices after changes; device IDs may change on reconnect. This development run returned no inputs, so a working driver cannot be claimed yet.
- **System meter silent:** confirm the interview playback goes to the Multi-Output Device and BlackHole is enabled there. A microphone never automatically captures system audio. Confirm you selected BlackHole as an input in this app.
- **Cannot hear interview:** the selected Multi-Output Device must also include your actual listening output. Follow BlackHole's official guide. macOS may disable the normal volume keys for Multi-Output; adjust physical-device levels yourself.
- **Echo/feedback:** use headphones and retain a physical meeting microphone. Do not route the mic into BlackHole or enable meeting monitoring through the system loopback.
- **Overflow/device stalled:** session stops safely. Close unnecessary workloads yourself, check sample rates and device connections, then test again. No unrelated process is stopped by this application.
- **Groq model unavailable:** choose an explicitly accessible model in `.env` and restart. No fallback is automatic. Models/list verifies access but does not establish that text generation works; a real answer is the final integration test.
- **API error:** check keys locally, account credits, network and model access. Error details are intentionally sanitized to avoid leaking secrets. Check the providers' dashboards. Never send key values in screenshots or chat.
- **Port 8765 occupied:** the launcher reports an error. Stop a previous assistant using its own Stop launcher/Terminal. Do not terminate an unrelated app; resolve that port conflict yourself.
- **Another tab controls session:** return to that tab or close it. Closing the controlling tab ends any live session before the backend releases control.
- **macOS blocks `.command`:** inspect the local text launcher and launch from Terminal if needed. The assistant does not approve security dialogs on your behalf.

## VS Code optimizations

Project-only `.vscode/settings.json` excludes `.venv`, caches and `.runtime` from watchers/search. Pylance indexing and automatic test discovery are disabled, diagnostics target open files, and Git autofetch is off. These are reversible by editing/removing the workspace settings. No global settings or extension enablement changed.

Installed extension folders include multiple AI assistants (Claude Code, Codeium, Gemini), Jupyter, Cloud Code, Data Cloud, Docker/Containers, Kubernetes, Vue tooling, GitLens and Git history/blame tools. If unused here, consider **Disable (Workspace)** for those extensions yourself. Installed folders do not prove that extensions are active or expensive; process inspection was restricted. Keep Python support, and optionally Pylance if desired. No recommendation to delete older version folders automatically.

## Verification

```sh
cd "$HOME/Interview Assistant"
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q app scripts tests
.venv/bin/python -m pytest -q
node --check app/static/app.js   # optional development check
node --check app/static/profile.js
.venv/bin/python scripts/measure.py
```

Tests cover question detection, duplicate suppression, PCM conversion at 16/44.1/48 kHz, callback bounds, API model checking with mocks, SDK wire protocol with fake transport, turn handling, cleanup, startup failure, timeout, stop during startup, local test mode, WebSocket security/ownership/recovery browser-disconnect and heartbeat cleanup, local PDF and DOCX preview, CV version management, experience calculation, role relevance, interview isolation, the offline price and billing gates, single-use authorisation, and selected backend deadlines. Mocked model tests cover the structured response contract and representative interview questions. They do not measure live model accuracy. A current Starlette/httpx deprecation warning affects its test adapter only; no extra package was added just to suppress it.

## Full interview copilot

In **Interview setup**, add your target role, job description and company research. Include sources and dates, and mark assumptions in your notes. Interview Copilot uses these supplied materials and your selected CV and master profile. It labels uncertainty in suggested responses and leaves missing company facts, salary expectations, notice periods and personal examples for you to confirm.

The shared answer strategy aims to improve responses even when you know the answer. It considers what the interviewer is assessing at the target seniority, leads with a clear point, and adds the strongest relevant evidence or an overlooked trade-off. For coding questions it weighs simpler and more efficient approaches against the constraints. For leadership and strategy it connects choices to ownership, communication and business impact. General recommendations and hypothetical examples stay distinct from verified personal experience.

Use **Written questions & coding challenges** to paste a prompt, capture a single screen or tab frame in a supporting browser, or upload an existing screenshot. macOS may request Screen Recording permission for the browser. Image text is extracted on the Mac using Apple Vision and the installed Xcode Command Line Tools compiler. Review the text before submitting. During an authorised live session the copilot sends only the reviewed text and relevant interview context to Groq and can suggest requirements, algorithms, code, debugging steps, tests, and complexity. The app renders code for review. Screen capture and OCR do not require a live session. [Apple Vision text recognition](https://developer.apple.com/documentation/vision/recognizing-text-in-images)

**Mock interview** works locally. Select a recruitment scenario or run the mixed interview, which starts with recruiter screening, follows up based on your response and chooses remaining topics based on the discussion before closing. Follow-up selection and basic feedback use local heuristics. Use the displayed rubric to judge factual accuracy and the quality of evidence yourself.

### Optional phone companion

The Mac continues to control audio capture, provider authorisation and STOP ALL. The phone can view spoken and written suggestions, review the latest interviewer prompt, submit a written question during an authorised session, extract text from an image and use local mock practice with the paired interview setup. Screen capture appears only where the phone browser exposes it. You can always upload a screenshot instead.

Open **Phone companion → Pair Phone** on the Mac. Scan its QR code on an iPhone connected to the same trusted Wi-Fi, then approve the matching device and comparison code on the Mac. The local-network URL and an alternative short code are also displayed. Invitations expire after two minutes; approved access lasts up to two hours. **Revoke Device** immediately ends access and closes the companion listener. Pairing again replaces the previous device.

The laptop stays on loopback. Pairing opens a separate companion-only listener bound to a selected private network address, with subnet, Host, Origin and authentication checks. No external hosting, forwarding or tunnel is used. Basic companion features work over local HTTP; HTTP does not encrypt traffic, so use a trusted private network. Browser screen capture requires a secure context and is hidden when unavailable; uploading an existing screenshot works without it. See [PHONE-SETUP.md](PHONE-SETUP.md) for instructions, security limits and verification status.


## Live source separation and latency

The phone separates system-audio **INTERVIEWER**, physical-microphone **YOU** and AI **SUGGESTED ANSWER**. It receives partial transcripts and answer drafts through an authenticated push stream. Interviewer instructions and coding constraints are retained with their question; clarification updates supersede obsolete generation. Candidate speech never triggers automatic answers. Spoken AI text appears first, with evidence/code/detail underneath.

Live latency diagnostics show stage timestamps on the Mac and phone. Audio remains 16 kHz PCM16 with 50 ms blocks, bounded queues and unchanged transcription turn-silence settings. [LIVE-PIPELINE.md](LIVE-PIPELINE.md) documents the measured local test results, streaming validation policy and limits of speaker-bleed detection. Physical iPhone and live-provider latency are not verified by mocked tests.


## Groq regression diagnostics

[REGRESSION-INVESTIGATION.md](REGRESSION-INVESTIGATION.md) distinguishes the unrecovered historical interviews from the confirmed local SDK incompatibility. The unsupported `stream_options` argument has been removed; tests now use the installed SDK against offline HTTP/SSE transport. Generation/error status is pushed to the phone and retained through cleanup. The production app saves its latest allowlisted failure metadata in owner-only `.runtime/last-groq-error.json`; no API keys, CV/transcript text or raw provider bodies are stored. No real provider verification was performed.


## Startup/settings recovery

Fresh pages start at 60 minutes and show the permitted 5–120-minute range. Invalid values now produce a specific message and a **Use 60 minutes** correction button. Temporary local settings failures retry automatically without starting providers. Stop waits for shutdown completion, and Start retries the app lock after an exiting process releases it. [STARTUP-REPAIR.md](STARTUP-REPAIR.md) records the real Desktop launcher tests and the confirmed causes.

The microphone selector defaults to **MacBook Pro Microphone** when available, ahead of an iPhone continuity microphone. Refreshing devices preserves your existing selection when it is still available.
