# Interview Assistant for macOS

A local, compact interview companion for an Apple Silicon Mac with 8 GB RAM. It captures your microphone and independently routed system audio, sends each to AssemblyAI Streaming v3, and asks Groq for suggested spoken answers to completed interviewer questions. All AI runs in the cloud. No audio recordings, database, Docker, Electron, local models or frontend build system.

## Quick start

1. Open `~/Interview Assistant/.env` in a text editor. Hidden files can be shown in Finder with Command–Shift–period. Add your own AssemblyAI and Groq API keys. Do not paste them into chat or JavaScript.
2. Leave `GROQ_MODEL=openai/gpt-oss-20b`, or explicitly select another accessible model. The backend checks the account's model list before opening transcription. It never silently substitutes models.
3. Double-click `~/Desktop/Start Interview Assistant.command`. Keep its Terminal open. Brave opens at http://127.0.0.1:8765.
4. Complete the audio routing below, select the two input devices, and use their independent **Test** buttons. Tests are local, last ten seconds, and make no cloud calls.
5. Press **Start session** when ready. Merely opening the application never starts capture or paid streaming.
6. **Stop** closes the audio/transcription session but leaves the app open. The Desktop **Stop Interview Assistant.command** exits the backend. Control-C in its Terminal also shuts it down cleanly. Use the stop launcher before editing keys; restart to reload `.env`.

The environment and dependencies are already installed on this Mac. No API keys have been supplied and no real cloud/audio integration has been validated. See SETUP-REMAINING.md and VERIFICATION.md.

## API configuration

Create keys in your own [AssemblyAI dashboard](https://www.assemblyai.com/dashboard) and [Groq console](https://console.groq.com/keys). Check account credits, model permissions and spend limits there.

`.env` supports:

```
ASSEMBLYAI_API_KEY=
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-20b
MAX_SESSION_MINUTES=120
ASSEMBLYAI_HOURLY_RATE=0.15
BROWSER_GRACE_SECONDS=15
```

`.env` is excluded from Git and created with owner-only read/write permissions (0600). Browser code receives only configured/not-configured flags. SDK errors are replaced with safe messages. Never enable HTTP/SDK debug logs when using real keys. The backend remains loopback-only and rejects other origins/hosts. It uses an HttpOnly, SameSite cookie for browser control and a separate local stop token. One tab owns a session at a time.

Groq's current production list shows `openai/gpt-oss-20b` at $0.075 input and $0.30 output per million tokens. It is the inexpensive default verified against the documentation during development, but account availability is checked at runtime. Output uses low reasoning effort for this model and bounded completion tokens. No web search/tools are enabled. See [Groq model documentation](https://console.groq.com/docs/models).

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

**Clear transcript** removes the current in-memory transcript and suggestions and cancels pending generation. Starting a new interview clears the previous one. A brief same-tab reconnect restores the current interview. If recovery expires, a new controller starts with an empty transcript. Audio and transcripts are never stored on disk.

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

## Usage estimates and shutdown

AssemblyAI uses `universal-streaming-english`. Engineering keyterms are enabled by default and can be switched off under **Interview setup**. At $0.15 base plus $0.04 for keyterms per channel-hour, two channels are approximately **$0.38 per elapsed hour**, or **$0.76 over 120 minutes**, excluding Groq. With keyterms switched off the estimate is **$0.60 over 120 minutes**. The estimate counts connection setup and teardown conservatively. Actual provider billing, taxes, account plans and rate changes may differ. Check the [AssemblyAI pricing page](https://www.assemblyai.com/pricing) and update the rate in `.env` if needed.

Billing is based on session duration, including silence. Muting your microphone does not close its paid session. The UI estimate is not a bill, budget cap or account balance. The 120-minute default is a duration limit, not a guarantee against charges during network failure. Provider dashboards are authoritative.

Both connections are terminated on Stop, timeout, fatal audio/transcription failure, graceful process shutdown or a controlling-browser disconnection lasting more than 15 seconds. The browser reconnects with short exponential backoff during that grace period and recovers the bounded snapshot. Initial AssemblyAI network connection has one bounded retry; established cloud sessions are not silently restarted after failure. Press Start yourself after fixing the problem. Groq errors can be retried with Suggest answer and do not repeatedly submit automatically. No app can send termination during a power failure or lost network; check provider usage after such an event.

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
- **Another tab controls session:** return to that tab, or close it and wait at least 15 seconds. Only one tab controls audio; reconnecting the same tab preserves ownership during the grace period.
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

Tests cover question detection, duplicate suppression, PCM conversion at 16/44.1/48 kHz, callback bounds, API model checking with mocks, SDK wire protocol with fake transport, turn handling, cleanup, startup failure, timeout, stop during startup, local test mode, WebSocket security/ownership/recovery browser-disconnect expiry, local PDF and DOCX preview, CV version management, experience calculation, role relevance, and interview isolation. Mocked model tests cover the structured response contract and representative interview questions. They do not measure live model accuracy. A current Starlette/httpx deprecation warning affects its test adapter only; no extra package was added just to suppress it.
