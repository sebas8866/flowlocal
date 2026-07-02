# Tasks: FlowLocal — Local Voice Dictation

**Feature**: specs/001-flowlocal-dictation · Stories: US1 = core dictation loop (P1), US2 = tray + settings UI (P2), US3 = autostart + first-run setup (P3)

## Phase 1 — Setup

- [x] T001 Create project skeleton: `flowlocal/` package, `requirements.txt`, `run_flowlocal.pyw`, `.gitignore` in repo root
- [x] T002 Create `.venv` with `py -3.11` and install requirements (+ nvidia cu12 libs for GPU)

## Phase 2 — Foundational

- [x] T003 [P] Config dataclass + JSON load/save with defaults, validation, atomic writes in flowlocal/config.py
- [x] T004 [P] Sound cues (start/stop/error via winsound) in flowlocal/sounds.py

## Phase 3 — US1: Core dictation loop (MVP)

- [x] T005 [P] [US1] Audio recorder (sounddevice InputStream, device enumeration) in flowlocal/recorder.py
- [x] T006 [P] [US1] Transcriber (faster-whisper, VAD on, CUDA→CPU fallback, lazy model load) in flowlocal/transcriber.py
- [x] T007 [P] [US1] Cleaner (stage-1 rules + stage-2 Ollama with timeout/fallback) in flowlocal/cleaner.py
- [x] T008 [P] [US1] Injector (clipboard save/set/paste/restore, failure fallback) in flowlocal/injector.py
- [x] T009 [P] [US1] Hotkey listeners (pynput keyboard+mouse, hold/toggle modes, binding parse/format, capture mode) in flowlocal/hotkey.py
- [x] T010 [US1] Pipeline worker + app wiring (serial queue, state callbacks, single-instance mutex) in flowlocal/app.py and flowlocal/__main__.py
- [x] T011 [US1] Unit tests for cleaner rules (fillers, stutter dedupe, casing) in tests/test_cleaner.py

## Phase 4 — US2: Tray + settings UI

- [x] T012 [P] [US2] Tray icon with state colors + menu (Settings/Pause/Quit) in flowlocal/tray.py
- [x] T013 [US2] Settings window (mic picker, trigger rebind capture, model preset, mode, cleanup toggles, autostart toggle, Ollama status) in flowlocal/settings_ui.py

## Phase 5 — US3: Autostart + setup script

- [x] T014 [P] [US3] HKCU Run registry management in flowlocal/autostart.py
- [x] T015 [US3] setup.ps1: venv creation, deps, model pre-download, GPU smoke test, autostart enable, optional Ollama hint

## Phase 6 — Polish

- [x] T016 Run quickstart validation scenarios 1–8; fix findings; git commit

## Dependencies

Setup → Foundational → US1 (T005–T009 parallel, then T010) → US2 (needs app states) → US3 → Polish. US2/US3 independent of each other.

**MVP** = end of Phase 3: dictation works via hardcoded-default trigger without tray/settings.
