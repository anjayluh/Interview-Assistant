# Delivery verification

## Implemented and installed

Project: `/Users/Angella/Interview Assistant`.
Desktop launchers: `/Users/Angella/Desktop/Start Interview Assistant.command` and `/Users/Angella/Desktop/Stop Interview Assistant.command` (owner executable).

```
Interview Assistant/
  .env                     owner-only; keys blank
  .env.example
  .gitignore
  .vscode/                 project-only settings/recommendation
  .venv/                   isolated installed environment
  app/
    config.py              environment settings
    audio.py               independent bounded capture and PCM conversion
    transcription.py       official AssemblyAI Streaming v3 adapter
    answers.py             official Groq client and experience guardrails
    questions.py           question heuristic and bounded deduplication
    session.py             lifecycle, timeout, context, audio tests, answer scheduling
    main.py                local HTTP/WebSocket control and security
    static/                HTML, CSS, vanilla JavaScript
  scripts/
    launch.py              single-instance launch and authenticated stop
    measure.py             reproducible idle resource measurement
  tests/                   unit, lifecycle, HTTP/WebSocket, SDK protocol tests
  requirements.txt
  requirements-dev.txt
  requirements.lock.txt
  pytest.ini
  resource-measurements.json
  README.md
  SETUP-REMAINING.md
  VERIFICATION.md
```

Direct runtime packages installed: FastAPI 0.162.0, Uvicorn 0.54.0, AssemblyAI 1.8.2, Groq 1.7.0, sounddevice 0.5.6, python-dotenv 1.2.4, soxr 1.1.0. NumPy 2.5.4 is installed for signal conversion, not local AI. Pytest 9.2.0 and httpx 0.28.1 support tests. Full transitive versions are pinned in requirements.lock.txt. No system packages or audio drivers were installed.

## Checks

- 28 automated tests passed; no remaining failing tests.
- Python compile/import checks passed.
- `pip check` passed: no broken requirements.
- JavaScript syntax check passed with Node; Node is not a runtime dependency.
- Both Desktop launcher shell syntax checks passed.
- Real Uvicorn server and WebSocket connection passed.
- Headless launcher startup, live health check, duplicate-start reuse and authenticated stop passed; backend exited with status 0. No unrelated process was terminated.
- Browser inspection showed the rendered interface, Connected locally, missing-key status, duration, device controls, transcript/answer panes and no-device explanation. No automatic capture occurred. Brave opening uses the installed macOS `open -a` mechanism; that GUI launcher path was not independently exercised.
- Local HTTP/WebSocket access restrictions, single controller, recovery and disconnection expiry were tested.
- Audio callbacks stayed within the eight-frame queue under 10,000 synthetic callbacks, without network operations.
- Real SDK audio send/Terminate wire behavior was tested using a fake network transport. Groq model validation and generation logic were tested with mocks.
- Initial browser-cancellation test failures were fixed and all checks rerun. One Starlette/httpx test-adapter deprecation warning remains; it is not a failing check.

## Actual measured resources

A real local Uvicorn backend with one connected WebSocket, idle for 5.000 seconds:

- Peak resident memory: **78.38 MiB**.
- CPU time during interval: **0.01474 seconds**.
- CPU use: **0.295% of one CPU core**.

Measured using macOS `getrusage` and Python process CPU time. The measurement client ran in the same process, so these figures include its overhead. Peak resident memory includes imports/startup; it is not current RSS. Browser memory is excluded. Live audio/transcription/generation load was not measured. These figures cannot establish performance under an actual interview workload. Run `scripts/measure.py` to reproduce the idle measurement; it makes no cloud API calls.

## Not verified / no claims of live success

- AssemblyAI authentication and real streaming: **not tested**, API key missing.
- Groq account model availability and real text generation: **not tested**, API key missing. The default model is listed in current official production documentation; startup checks the actual account's model list before paid transcription.
- Physical microphone capture: **not tested**, Core Audio enumerated zero input devices here.
- BlackHole/system capture and audible playback routing: **not tested**, same device-access limitation. BlackHole 0.2.11 driver files are present; that does not verify driver operation or routing.
- Actual active-session CPU/RAM: **not measured**.

No credentials were retrieved from any other application. No API credit was spent by development tests. No macOS audio configuration, privacy permission, default input/output, Multi-Output Device, global VS Code setting or extension enablement was changed. No startup-at-boot configuration was created.

Only the credential entry, macOS permission/routing decisions and final physical/cloud check in SETUP-REMAINING.md require the user's involvement.
