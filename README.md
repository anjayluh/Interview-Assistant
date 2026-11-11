# Interview Assistant for macOS

A local, compact interview companion for an Apple Silicon Mac with 8 GB RAM. It captures your microphone and independently routed system audio, sends each to AssemblyAI Streaming v3, and asks Groq for suggested spoken answers to completed interviewer questions. All AI runs in the cloud. No audio recordings, database, Docker, Electron, local models or frontend build system.

## Quick start

1. Double-click `~/Desktop/Start Interview Assistant.command`. Brave opens at http://127.0.0.1:8765 in **LOCAL-ONLY** mode.
2. Manage CVs, edit the professional profile and prepare an interview. These operations require no keys and make no provider requests.
3. For audio routing, select the microphone and system input and run their separate **Test** buttons. Each ten-second test stays on your Mac.
4. Leave the cap at **UGX 0** if you have no budget. Review **Live session safety** only if you intend to authorise paid services.
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
7. Test microphone: speak, verify its meter moves. Test system audio: play ordinary speech in Brave, verify the second meter moves and you can still hear it. Do these before starting a paid session.

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

## Live session safety

The app starts in **LOCAL-ONLY** mode. Opening a page, using the CV and profile tools, preparing an interview, calculating an estimate, and testing audio make no requests to AssemblyAI or Groq.

To authorise one live session, select two distinct audio inputs, choose a duration, and enter a maximum transcription estimate in UGX. The duration defaults to **5 minutes**. `MAX_SESSION_MINUTES` is the optional server-side ceiling (120 minutes by default). The app starts its deadline before the first provider call, including connection setup.

The quote uses AssemblyAI's published Universal-Streaming English rate of **US$0.15 per audio channel-hour**, checked **11 November 2026**. Optional keyterms are off by default and add **US$0.04 per channel-hour** when enabled. Multiply the per-channel rate by two streams and the session minutes divided by 60. Five minutes costs an estimated **US$0.025** at the base rate or about **US$0.0317** with keyterms. The UGX estimate uses the exchange rate you enter and rounds up to a whole shilling. The estimate assumes both streams run for the entire selected duration and excludes Groq, taxes, conversion fees and any billing after disconnect. [AssemblyAI pricing](https://www.assemblyai.com/pricing)

Enter a checked UGX-per-USD rate with its source and date. Exchange rates older than seven days, future dates and missing conversion information block live sessions. The published pricing snapshot expires after 30 days and must be refreshed from the provider in `app/cost.py` before the app allows new sessions. If an estimate cannot be calculated, the app displays **COST UNKNOWN — LIVE SESSION BLOCKED**. The default **UGX 0** cap blocks all non-zero estimates, even when credits may be available.

Check billing status, any free allowance and remaining credits in **both** provider dashboards, then confirm those checks in the app. The app does not verify account billing and does not assume free access. The app also requires configured API keys, a fresh single-use quote within your limit, and a separate confirmation for the specific live session. The server checks the same inputs again before calling either provider.

**STOP ALL**, the backend deadline, provider failure, browser disconnection, missing browser heartbeats for 15 seconds, and graceful backend shutdown cancel pending answer requests and stop both audio sources and AssemblyAI connections. The app clears session-only data and returns to local-only mode. AssemblyAI handshakes and Groq requests have zero automatic retries. Reconnecting the browser restores the local interface without resuming a provider session.

The cap is an estimate used for admission. The app cannot enforce provider billing, guarantee cancellation of a request already received by Groq, or guarantee that a broken network lets the provider receive termination. Account dashboards and provider-side limits are authoritative.

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

Tests cover question detection, duplicate suppression, PCM conversion at 16/44.1/48 kHz, callback bounds, API model checking with mocks, SDK wire protocol with fake transport, turn handling, cleanup, startup failure, timeout, stop during startup, local test mode, WebSocket security/ownership/recovery browser-disconnect and heartbeat cleanup, local PDF and DOCX preview, CV version management, experience calculation, role relevance, interview isolation, the offline price and billing gates, single-use authorisation, and the five-minute backend deadline. Mocked model tests cover the structured response contract and representative interview questions. They do not measure live model accuracy. A current Starlette/httpx deprecation warning affects its test adapter only; no extra package was added just to suppress it.
