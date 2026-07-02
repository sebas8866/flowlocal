# FlowLocal — local voice dictation (Wispr Flow clone)

Personal Windows tray app: push-to-talk (mouse side button) → faster-whisper local STT → filler/grammar cleanup → paste into focused app. Fully offline, zero cost.

- Python 3.11 venv at `.venv` — always use `.venv\Scripts\python`, never bare `python`.
- Config lives at `%APPDATA%\FlowLocal\config.json` (see specs contract).
- No cloud calls anywhere; Ollama on localhost is the only external process allowed.

<!-- SPECKIT START -->
Active plan: [specs/001-flowlocal-dictation/plan.md](specs/001-flowlocal-dictation/plan.md)
<!-- SPECKIT END -->
