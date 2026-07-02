# FlowLocal

Fully local, free voice dictation for Windows — a self-hosted alternative to Wispr Flow.

Hold a key or your mouse's side button, speak, release — clean, punctuated text is pasted into whatever app has focus. Speech-to-text runs on your machine with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (Whisper large-v3-turbo), and transcripts are cleaned up automatically: filler words removed, grammar fixed, false starts and repetitions collapsed — optionally using a local [Ollama](https://ollama.com) LLM for higher-quality rewrites. No audio or text ever leaves the machine.

- **100% offline** after the first model download
- **No accounts, no subscriptions, no telemetry**
- Push-to-talk from **any keyboard key or mouse side button**
- Runs quietly in the **system tray**, with a Wispr-style waveform pill at the bottom of the screen
- Auto-starts with Windows

## Requirements

- Windows 10 or 11
- Python 3.11, installed with the `py` launcher (get it from [python.org](https://www.python.org/downloads/) — check "Install launcher for all users")
- ~3 GB free disk space (Whisper model + optional CUDA runtime libraries)
- NVIDIA GPU — optional but recommended for fast transcription; CPU works fine, just slower
- Optional: [Ollama](https://ollama.com/download) with `qwen2.5:7b-instruct` pulled (or `qwen2.5:3b-instruct` on smaller GPUs) for the best cleanup quality — FlowLocal works fully without it, using rule-based cleanup only

## Install

```powershell
git clone https://github.com/sebas8866/flowlocal.git
cd flowlocal
.\setup.ps1
```

`setup.ps1` creates a `.venv`, installs dependencies, and on first run downloads:

- The Whisper `large-v3-turbo` model (**~1.6 GB**, one-time, cached locally afterwards)
- NVIDIA CUDA 12 pip wheels for GPU acceleration (only if an NVIDIA GPU is detected)

Both are one-time downloads — everything after that is fully offline.

## Usage

Run FlowLocal with:

```powershell
.venv\Scripts\pythonw.exe run_flowlocal.pyw
```

(or just let it auto-start on the next login — autostart is enabled by `setup.ps1`).

- **Default trigger**: mouse forward/side button (X2) — hold it, speak, release.
- **Tray icon** → *Settings* to change microphone, trigger binding, model, language, and cleanup toggles.
- A **bottom waveform pill** shows the current state (idle / recording / transcribing). Hover it after a dictation to copy the last result.
- If text can't be pasted into the focused window (e.g. an elevated app), a small popup appears — the text stays on your clipboard so you can paste it manually.

## Configuration

Settings persist to `%APPDATA%\FlowLocal\config.json`. Most fields are editable from the Settings window; you can also hand-edit the file (changes are picked up on next app start). Full contract: [specs/001-flowlocal-dictation/contracts/config-schema.md](specs/001-flowlocal-dictation/contracts/config-schema.md).

| Field | Type | Default | Notes |
|---|---|---|---|
| `trigger` | str | `"mouse:x2"` | `mouse:x1`, `mouse:x2`, or `key:<combo>` (e.g. `key:f9`, `key:ctrl_l+space`) |
| `mode` | str | `"hold"` | `hold` (push-to-talk) or `toggle` |
| `mic_device` | int \| null | `null` | sounddevice index; `null` = system default |
| `model` | str | `"large-v3-turbo"` | `large-v3-turbo`, `distil-large-v3`, or `small` |
| `language` | str \| null | `null` | `null` = auto-detect |
| `clean_fillers` | bool | `true` | stage-1 rule-based cleanup |
| `clean_llm` | bool | `true` | stage-2 Ollama rewrite (no-op if Ollama isn't running) |
| `ollama_model` | str | `"qwen2.5:7b-instruct"` | model name pulled in Ollama |
| `filler_words` | list[str] | see below | user-editable list of words stripped in stage 1 |
| `autostart` | bool | `true` | mirrors the Windows Run registry entry |
| `sounds` | bool | `true` | audible start/stop/error cues |
| `max_record_seconds` | int | `300` | hard stop safeguard |
| `show_overlay` | bool | `true` | show the bottom waveform pill |

Default `filler_words`: `um, uh, uhm, er, ah, like, you know, i mean, sort of, kind of` — edit the list in `config.json` or Settings to add/remove words.

## Troubleshooting

- **Logs**: `%APPDATA%\FlowLocal\flowlocal.log`
- **No GPU / CUDA init fails**: FlowLocal automatically falls back to CPU (`int8`) — transcription still works, just slower.
- **Ollama not detected**: cleanup falls back to rule-based only (fillers/stutters removed, but no grammar rewrite or restart collapse). Install Ollama and run `ollama pull qwen2.5:7b-instruct` at any time; it's auto-detected on next dictation.
- **Hotkey doesn't fire in some app**: global hooks (pynput) can't reach windows running elevated (as Administrator). Run FlowLocal elevated too, or avoid dictating into elevated windows.
- **Waveform pill on the wrong screen / missing in multi-monitor setups**: the pill only renders on the primary monitor; this is a known limitation.
- **Per-monitor DPI**: mixed-DPI multi-monitor setups may show the pill slightly mis-scaled — cosmetic only.

## Uninstall

1. Quit FlowLocal from the tray icon.
2. Remove the autostart entry: delete the `FlowLocal` value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (or toggle "Autostart" off in Settings before quitting).
3. Delete the project folder and `%APPDATA%\FlowLocal`.
4. Optional: the Whisper model is cached in the Hugging Face cache (`%USERPROFILE%\.cache\huggingface`) — delete it there if you want the disk space back and don't use it for anything else.

## License

[MIT](LICENSE)
