# FlowLocal — contributor/agent guide

Windows tray app: push-to-talk (keyboard or mouse side button) → faster-whisper local speech-to-text → filler/grammar cleanup → paste into the focused app. Fully offline, zero cost, no cloud dependency.

## Environment

- Python 3.11 in a project-local venv at `.venv` — always invoke `.venv\Scripts\python`, never a bare `python`.
- Set up with `.\setup.ps1` (creates the venv, installs dependencies, downloads the default Whisper model, enables autostart).

## Rules

- **Local by default.** The only permitted external hosts are Ollama on `127.0.0.1` (optional, for transcript cleanup) and `api.groq.com` — and the latter only when `cfg.backend == "cloud"` and the user has supplied their own API key. Never add a network call to any other host, and never add telemetry.
- Config lives at `%APPDATA%\FlowLocal\config.json` — see [specs/001-flowlocal-dictation/contracts/config-schema.md](specs/001-flowlocal-dictation/contracts/config-schema.md) for the full field contract. The app must never crash on a missing or malformed config; invalid fields reset to defaults.

## Tests

```powershell
.venv\Scripts\python.exe -m unittest discover tests
```

<!-- SPECKIT START -->
Active plan: [specs/001-flowlocal-dictation/plan.md](specs/001-flowlocal-dictation/plan.md)
<!-- SPECKIT END -->
