# Interview Assistant for macOS

A local, compact interview companion for an Apple Silicon Mac with 8 GB RAM. It captures your microphone and independently routed system audio, sends each to AssemblyAI Streaming v3, and asks Groq for suggested spoken answers to completed interviewer questions. All AI runs in the cloud. No audio recordings, database, Docker, Electron, local models or frontend build system.

## Quick start

1. Double-click `~/Desktop/Start Interview Assistant.command`. Brave opens at http://127.0.0.1:8765 in **LOCAL-ONLY** mode.
2. Manage CVs, edit the professional profile and prepare an interview. These operations require no keys and make no provider requests.
3. For audio routing, select the microphone and system input and run their separate **Test** buttons. Each ten-second test stays on your Mac.
4. Keep the cap at **UGX 0**. Open **Live session safety** to confirm free balances and zero-billing protection before starting a free live session.
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

## Free-only live sessions

The app starts in **LOCAL-ONLY** mode. CVs, profile editing, interview preparation, local mock interviews, image text extraction and audio tests make no AssemblyAI or Groq requests.

**FREE USAGE ONLY** keeps maximum user payment at **UGX 0**. The app distinguishes gross provider usage from free-credit consumption and expected payment. A non-zero gross amount can be covered by free allowances. Exchange rates are optional and never required for a fully covered free session.

### What to check in your accounts

Use the account or organisation attached to the configured API key. Check this yourself in the dashboard before each session.

* AssemblyAI must be an **unupgraded free trial**. Check the remaining promotional credit, the exact account rate for `universal-streaming-english` and any enabled keyterm surcharge. Verify that the credit covers both simultaneous streams and the selected features. Confirm there is no payment method, auto-pay, invoice agreement or route to paid credit. The app blocks upgraded and billable plans. [AssemblyAI account and billing documentation](https://www.assemblyai.com/docs/faq/how-to-get-your-api-key)
* Groq must be on the **Free plan** for the selected model. Check its remaining daily token and request quotas, shorter rate limits and the reset time. Confirm there is no paid plan, auto-reload or invoicing arrangement. Groq documents downgrading under **Settings → Billing**, which may first require payment of outstanding Developer-plan usage. A Developer-plan spending limit alone cannot establish zero-charge protection because its spend tracking is delayed. Promotional credits on billable plans are also blocked. [Groq billing FAQs](https://console.groq.com/docs/billing-faqs), [spend limits](https://console.groq.com/docs/spend-limits), [rate limits](https://console.groq.com/docs/rate-limits)

Enter each current balance and its dashboard or support reference in **Live session safety**, then check the confirmation boxes. Set a date/time through which both allowances will remain available, including 30 seconds after the session. Reserve the balances for this interview by pausing other applications and organisation members' use of them. Keep billing settings fixed during the session. Confirmations must be no more than 15 minutes old and are cleared when a session ends. No account balance, payment method or billing tier is inferred from the API key.

The app does not read balances through a billing API. These fields represent your account-specific confirmation. The application cannot prevent someone upgrading the organisation elsewhere or make promises about a provider's billing system. If you cannot establish that the provider account cannot bill, keep the session blocked.

### Reservation calculation

Both audio sources use separate AssemblyAI Streaming v3 connections. Let `r` be your confirmed USD per channel-hour rate including optional keyterms. Gross transcription usage is `r × 2 × minutes / 60`. Required free-credit coverage is `r × 2 × (minutes × 60 + 30) / 3600 × 1.25`. This adds a 25% margin and a 30-second shutdown allowance. For illustration only, if your account shows a base rate of US$0.15, five minutes has US$0.025 gross usage and needs US$0.034375 free credit reserved. Your actual rate must come from the account. Current public pricing no longer lists the app's legacy English model, so the app does not reuse its earlier public price automatically.

Groq defaults to at most **three answers** per session. You can deliberately change that bound before authorising. Each answer reserves **32,768 input tokens and 2,000 output/reasoning tokens**. The app checks a conservative UTF-8 byte-based input bound before sending. It reserves a further **25% token margin**, and **25% more requests than the answer count plus one metadata-access check**. For three answers this reserves **130,380 tokens and five requests**. Groq gross usage is calculated separately using the published prices for the configured GPT-OSS 20B or 120B model. Both currently supported gross price schedules were checked on 19 November 2026. Other models require a verified price schedule before admission. [Groq model prices](https://console.groq.com/docs/models)

The app reserves before requesting and does not refund reservations after failure or cancellation. Once the chosen answer bound has been consumed, another answer request stops the session and asks you to reconfirm current balances. Provider free-plan rate limits can also interrupt a session even when daily quota is sufficient. Quota, credit and access failures stop the session without retries or a paid fallback. The app tracks its local reservations rather than claiming they are live provider balances.

### Starting and stopping

The selected duration defaults to **five minutes**. `MAX_SESSION_MINUTES` remains an optional server-side ceiling of 120 minutes. The deadline starts before the first provider request, includes connection setup, and is enforced by the backend. Sufficient free coverage for both providers, protected account types, current confirmation, configured keys, two distinct input devices and explicit consent to a fresh single-use quote are required.

**STOP ALL**, the deadline, exhausted reservations, provider failure, laptop-browser disconnection, missing laptop heartbeats for 15 seconds and graceful backend shutdown stop capture and close provider connections. Pending Groq generation is cancelled where supported and temporary interview data is cleared. Reopening the page does not resume a live session. Local tools remain available.

All free balances and zero-charge billing protection for the user's actual accounts are **unverified by the application** until the user establishes them. The app can enforce its admission, request and time bounds. Only provider-side account protections can prevent actual charges independently. In-flight usage, delayed metering, network or power failure and external changes to account billing can exceed a local estimate. The free-only path can be used when an unupgraded AssemblyAI trial and Groq Free organisation with adequate confirmed balances are established. It does not convert a billable account into a free one.

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

## Full interview copilot

In **Interview setup**, add your target role, job description and company research. Include sources and dates, and mark assumptions in your notes. Interview Copilot uses these supplied materials and your selected CV and master profile. It labels uncertainty in suggested responses and leaves missing company facts, salary expectations, notice periods and personal examples for you to confirm.

The shared answer strategy aims to improve responses even when you know the answer. It considers what the interviewer is assessing at the target seniority, leads with a clear point, and adds the strongest relevant evidence or an overlooked trade-off. For coding questions it weighs simpler and more efficient approaches against the constraints. For leadership and strategy it connects choices to ownership, communication and business impact. General recommendations and hypothetical examples stay distinct from verified personal experience.

Use **Written questions & coding challenges** to paste a prompt, capture a single screen or tab frame in a supporting browser, or upload an existing screenshot. macOS may request Screen Recording permission for the browser. Image text is extracted on the Mac using Apple Vision and the installed Xcode Command Line Tools compiler. Review the text before submitting. During an authorised live session the copilot sends only the reviewed text and relevant interview context to Groq and can suggest requirements, algorithms, code, debugging steps, tests, and complexity. The app renders code for review. Screen capture and OCR do not require a live session. [Apple Vision text recognition](https://developer.apple.com/documentation/vision/recognizing-text-in-images)

**Mock interview** works locally. Select a recruitment scenario or run the mixed interview, which starts with recruiter screening, follows up based on your response and chooses remaining topics based on the discussion before closing. Follow-up selection and basic feedback use local heuristics. Use the displayed rubric to judge factual accuracy and the quality of evidence yourself.

### Optional phone companion

The Mac continues to control audio capture, provider authorisation and STOP ALL. The phone can view spoken and written suggestions, review the latest interviewer prompt, submit a written question during an authorised session, extract text from an image and use local mock practice with the paired interview setup. Screen capture appears only where the phone browser exposes it. You can always upload a screenshot instead.

The companion requires a trusted HTTPS reverse proxy you control. Set `IA_PHONE_ORIGIN` to its exact HTTPS origin, such as `https://interview.example.org` (no trailing slash), and restart the Mac backend. Forward only `/companion`, `/static/copilot.js`, `/static/style.css`, `/api/companion/*`, and `/api/copilot/{scenarios,practice,ocr}` to `http://127.0.0.1:8765`. Preserve the phone-facing Host and Origin headers. Keep the laptop backend on loopback. Restrict access to your private network or authenticated proxy and use a certificate trusted by your phone.

On the laptop, open **Phone companion → Create pairing link** after setting your interview context. Open the private link on the phone. It expires after two hours and creating a new link invalidates the previous one. Use **Revoke link** to end phone access sooner. Keep the laptop app open through the session. Losing the laptop connection ends live capture under the existing safety rules.
