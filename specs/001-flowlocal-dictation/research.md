# Research: FlowLocal

## R1 — Speech-to-text model

**Decision**: faster-whisper (CTranslate2) with three user-selectable presets:
- `large-v3-turbo` (default, "Accurate") — ~809M params, near large-v3 accuracy at ~6× speed; float16 on GPU, int8 on CPU
- `distil-large-v3` ("Fast") — English-optimized distillation, faster still
- `small` ("Light") — for CPU-only situations

**Rationale**: Whisper large-v3-turbo is the best free, local, multilingual STT with mature Windows tooling. faster-whisper is the fastest maintained runtime with built-in VAD (`vad_filter=True` kills the "accidental press → garbage" edge case) and automatic punctuation/casing, which does half the cleanup for free. Models auto-download from Hugging Face once, then cache locally (offline afterwards).

**Alternatives considered**:
- *NVIDIA Parakeet TDT 0.6B* — tops English ASR leaderboards and is extremely fast, but NeMo/onnx tooling on Windows is heavier and it's English-only; kept in mind as a future preset.
- *whisper.cpp* — great, but Python bindings are less ergonomic than faster-whisper and we're Python anyway.
- *Vosk* — much lower accuracy; rejected.

**Blackwell note**: RTX 5060 = sm_120, needs CUDA 12.8+ kernels. Latest `ctranslate2` wheels ship CUDA 12 support; if `WhisperModel(..., device="cuda")` raises at load or first inference, we catch and rebuild with `device="cpu", compute_type="int8"`. Turbo on CPU int8 still transcribes ~10 s of audio in a few seconds.

## R2 — Transcript cleanup (Wispr-Flow-style)

**Decision**: two-stage pipeline, each stage toggleable (FR-004):
1. **Rules (always available, default on)**: regex filler removal (`um, uh, uhm, er, ah, like, you know, i mean, sort of, kind of` — word-boundary, case-insensitive, list user-editable in config), immediate token-repeat dedupe ("send send"→"send", "to to"→"to"), whitespace/punctuation normalization, sentence capitalization. Whisper itself supplies punctuation and casing.
2. **Local LLM rewrite (optional, on when available)**: POST to Ollama `localhost:11434/api/generate`, default model `qwen2.5:7b-instruct`, strict prompt: "rewrite this dictation: remove fillers, fix grammar, collapse false starts and self-corrections into the intended sentence; output ONLY the rewritten text". Timeout 10 s → fall back to stage-1 output. Availability probed at startup and shown in settings.

**Rationale**: rules are instant and dependency-free; grammatical restart collapse ("Let's meet at— actually Thursday") genuinely needs a language model, and Ollama is the most robust free local-LLM runtime on Windows (own installer, manages GPU). Qwen2.5-3B-instruct is small, fast on an RTX 5060, and strong at rewrite tasks. Everything stays on-device (SC-004/005).

**Alternatives considered**: *llama-cpp-python* embedded (CUDA wheel install on Windows is fragile); *transformers + small T5 grammar model* (weaker at restart collapse); *cloud LLM* (violates the whole point).

## R3 — Global trigger incl. mouse side buttons

**Decision**: `pynput` — one `keyboard.Listener` + one `mouse.Listener`, both global. Mouse side buttons arrive as `Button.x1` / `Button.x2` on Windows. Binding stored as a tagged string (`"mouse:x2"` or `"key:ctrl+alt+space"` etc.). Settings has a "press your new trigger…" capture mode reusing the same listeners.

**Rationale**: pynput is the only mainstream pure-Python lib that cleanly captures both global keyboard and mouse x-buttons with press *and* release events (needed for push-to-talk hold semantics). The `keyboard` lib doesn't do mouse; `mouse` lib is unmaintained.

## R4 — Audio capture

**Decision**: `sounddevice` InputStream, 16 kHz mono float32, frames accumulated in a list of numpy blocks; device index from config, `sd.query_devices()` for the picker, WASAPI default host API.

**Rationale**: PortAudio wheels bundled, rock-solid on Windows, numpy output feeds faster-whisper directly (no temp WAV needed).

## R5 — Text injection

**Decision**: pywin32 clipboard: `OpenClipboard → save existing CF_UNICODETEXT → SetClipboardData(text) → close`, then pynput `Controller` sends Ctrl+V, then after 300 ms restore the saved clipboard. If any step throws, leave the dictated text on the clipboard and toast "Paste failed — text is on your clipboard" (FR-012).

**Rationale**: identical to Wispr Flow's mechanism; character-by-character SendInput typing is 100× slower and breaks with non-ASCII; UI Automation insertion doesn't work in all apps.

## R6 — Tray + settings UI

**Decision**: `pystray` with Pillow-drawn state icons (gray idle / red recording / amber transcribing); tkinter/ttk settings window opened on demand in a separate thread-safe way (tk mainloop on the main thread, pystray detached).

**Rationale**: zero heavyweight GUI deps; tkinter ships with Python; settings UI is a simple form (combo boxes + checkboxes + capture button) — SC scope doesn't justify PySide6.

## R7 — Autostart + single instance

**Decision**: registry value `FlowLocal` in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` = `"<venv>\pythonw.exe" "<project>\run_flowlocal.pyw"`. Single instance via `win32event.CreateMutex("Global\\FlowLocal")` — second launch shows a toast and exits.

**Rationale**: HKCU Run needs no admin, is trivially toggleable (FR-008), and pythonw gives a windowless process.

## R8 — Dependencies (requirements.txt)

```
faster-whisper>=1.1
sounddevice>=0.5
numpy
pynput>=1.7
pystray>=0.19
Pillow
pywin32
requests        # Ollama client
```
Python 3.11 venv. GPU path additionally needs NVIDIA driver only (CTranslate2 wheels bundle cuBLAS/cuDNN via pip extras `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` — installed by setup.ps1, with CPU fallback if unavailable).
