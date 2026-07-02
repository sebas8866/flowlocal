# Implementation Plan: FlowLocal — Local Voice Dictation

**Feature**: `specs/001-flowlocal-dictation` · **Spec**: [spec.md](spec.md) · **Date**: 2026-07-02

## Technical Context

- **Target**: Single Windows 11 machine, NVIDIA RTX 5060 (Blackwell), Python 3.11 via `py -3.11`.
- **Language**: Python 3.11 in a project-local `.venv`.
- **Constitution**: none exists for this project — gates N/A.

## Architecture

One long-running Python process, launched by `pythonw.exe` (no console), living in the system tray. Event-driven pipeline:

```
[pynput global listener: keyboard + mouse x-buttons]
        │ trigger down/up (or toggle)
        ▼
[Recorder: sounddevice InputStream, 16 kHz mono f32, selected device]
        │ numpy audio buffer on stop
        ▼
[Transcriber: faster-whisper, model per settings, CUDA→CPU auto-fallback]
        │ raw text
        ▼
[Cleaner: stage 1 rules (fillers, stutters, spacing) → stage 2 optional local LLM (Ollama) for grammar + restart-collapse]
        │ final text
        ▼
[Injector: save clipboard → set text → SendInput Ctrl+V → restore clipboard]
```

A worker thread owns the pipeline; the trigger listener only signals it (queue), so rapid dictations serialize (edge case: no interleaving). Tray icon (pystray) reflects state: idle / recording / transcribing. Sound cues (winsound) on start/stop.

### Module layout

```
FlowLocal/
├── flowlocal/
│   ├── __main__.py        # entry point: single-instance guard, wiring, tray loop
│   ├── config.py          # Config dataclass, JSON load/save (%APPDATA%\FlowLocal\config.json)
│   ├── hotkey.py          # pynput keyboard+mouse listeners, binding capture mode for rebind UI
│   ├── recorder.py        # sounddevice capture, device enumeration
│   ├── transcriber.py     # faster-whisper wrapper, model download/cache, device fallback
│   ├── cleaner.py         # rule-based cleanup + optional Ollama rewrite
│   ├── injector.py        # clipboard save/set/paste/restore (pywin32 + SendInput)
│   ├── tray.py            # pystray icon, state icons, menu
│   ├── settings_ui.py     # tkinter/ttk settings window (mic, trigger, model, toggles)
│   ├── autostart.py       # HKCU\...\Run registry entry management
│   └── sounds.py          # start/stop/error cues
├── run_flowlocal.pyw      # launcher for pythonw / autostart
├── setup.ps1              # one-shot: create .venv, install deps, first-run model fetch, enable autostart
├── requirements.txt
└── specs/…
```

## Key Decisions (details in research.md)

| Area | Decision |
|---|---|
| STT | **faster-whisper** with `large-v3-turbo` default (GPU), `distil-large-v3` and `small` as presets; CTranslate2 CUDA 12 wheels; auto-fallback to CPU int8 if CUDA init fails on Blackwell |
| Cleanup | Stage 1 always-on rules (filler regexes, immediate-repeat dedupe, spacing/casing fixes). Stage 2 optional: **Ollama** at localhost (default model `qwen2.5:7b-instruct`) rewrites for grammar + false-start collapse. Auto-detected; app fully functional without it |
| Trigger | **pynput** — `mouse.Listener` exposes `Button.x1/x2`; keyboard listener for key bindings; special "capture next input" mode for rebinding in settings |
| Audio | **sounddevice** (PortAudio), 16 kHz mono float32; `query_devices()` for the mic picker |
| Injection | pywin32 clipboard (save → set → restore after 300 ms) + `SendInput` Ctrl+V via pynput controller; on failure text stays on clipboard (FR-012) |
| Tray/UI | **pystray** + Pillow-drawn icons; **tkinter/ttk** settings window (stdlib, zero extra deps) |
| Autostart | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` → `pythonw.exe run_flowlocal.pyw` |
| Single instance | Named mutex via pywin32 (`CreateMutex`) |
| Config | JSON at `%APPDATA%\FlowLocal\config.json`, dataclass-validated, atomic writes |

## Risks & Mitigations

1. **Blackwell (sm_120) vs CTranslate2 CUDA** — newest ctranslate2 wheels target CUDA 12.x; if kernel init fails, transcriber catches and retries on CPU `int8` (turbo model still near-real-time on modern CPUs). Verified at setup time by `setup.ps1` smoke test.
2. **Cleanup quality (SC-002)** — rules alone catch fillers/stutters but not grammatical restarts; Ollama stage covers that. Setup script offers Ollama install + model pull; if declined, SC-002 measured with rules-only.
3. **`keyboard`-style global hooks vs games/elevated apps** — pynput hooks don't reach elevated windows; acceptable for a personal tool (documented limitation).
4. **Clipboard races** — injector serializes through the single worker thread and restores clipboard on a timer only after paste completes.

## Phase 1 artifacts

- [data-model.md](data-model.md) — Config + DictationSession entities
- [contracts/config-schema.md](contracts/config-schema.md) — persisted config contract
- [quickstart.md](quickstart.md) — setup + end-to-end validation runs
