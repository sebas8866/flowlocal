# Contract: config.json

Location: `%APPDATA%\FlowLocal\config.json` — the only persisted interface. Example:

```json
{
  "trigger": "mouse:x2",
  "mode": "hold",
  "mic_device": null,
  "mic_device_name": null,
  "model": "large-v3-turbo",
  "language": null,
  "clean_fillers": true,
  "clean_llm": true,
  "ollama_model": "qwen2.5:7b-instruct",
  "filler_words": ["um", "uh", "uhm", "er", "ah", "like", "you know", "i mean", "sort of", "kind of"],
  "autostart": true,
  "sounds": true,
  "max_record_seconds": 300,
  "show_overlay": true,
  "backend": "local",
  "groq_api_key": "",
  "cloud_stt_model": "whisper-large-v3-turbo",
  "cloud_llm_model": "llama-3.3-70b-versatile",
  "vocabulary": [],
  "smart_context": true,
  "voice_commands": true,
  "theme": "light",
  "save_history": true
}
```

Guarantees:
- App never crashes on malformed/missing config — bad fields revert to defaults and file is rewritten.
- Hand-edits are picked up on next app start (settings UI edits apply immediately).
- `trigger` grammar: `mouse:x1 | mouse:x2 | key:<key>[+<key>...]` where keys use pynput canonical names (e.g. `key:ctrl_l+space`, `key:f9`).
- `vocabulary`: list of non-empty strings (personal dictionary); any other shape reverts the whole list to `[]`. Used both as Whisper `initial_prompt` bias and in the stage-2 rewrite prompt.
- `mic_device_name`: preferred microphone by name (survives PortAudio device-index reshuffles across reboots/replugs); resolved to an index at recording-start time, falling back to `mic_device`/default if the named device isn't currently present.
- `smart_context`: when `true`, the foreground app + window title is captured at trigger-press time and passed to the stage-2 rewrite prompt so tone can match the target app. `false` disables capture entirely.
- `voice_commands`: when `true`, enables the "new line"/"new paragraph" stage-1 rule and the "scratch that"/"delete that"/"undo that"/"undo last" whole-utterance undo command.
- `theme`: one of `light | dark | system`; controls the CustomTkinter appearance mode of the app window. Invalid values revert to `"light"`.
- `save_history`: when `true`, each successful dictation (text + audio seconds) is appended to the local-only history store at `%APPDATA%\FlowLocal\history.json` (see the app window's History page). `false` disables recording entirely; existing history is left untouched.

# Contract: Ollama cleanup call (optional external interface)

`POST http://127.0.0.1:11434/api/generate` with `{"model": cfg.ollama_model, "prompt": <cleaner.build_rewrite_prompt(...) output>, "stream": false, "options": {"temperature": 0.2}}` → response `.response` used verbatim as cleaned text. 30 s timeout; any failure ⇒ fall back to stage-1 text. Never called when `clean_llm` is false. The prompt conditionally includes a vocabulary section (when `cfg.vocabulary` is non-empty), an app-context section (when the caller passes `app_context`), and a previous-dictation section (when the caller passes `previous`, i.e. the last injected text landed <120s ago).

# Contract: Groq cloud calls (optional external interface)

Only called when `cfg.backend == "cloud"` and `cfg.groq_api_key` is non-empty.

- **STT**: `POST https://api.groq.com/openai/v1/audio/transcriptions`, multipart form with `model=cfg.cloud_stt_model`, `response_format=text`, `language=cfg.language` (omitted when `null`/auto), `prompt=<vocabulary + previous-dictation bias>` (omitted when empty), `file=("audio.wav", <16-bit PCM WAV bytes>, "audio/wav")`, header `Authorization: Bearer <cfg.groq_api_key>`. 20 s timeout. Response body (plain text) used verbatim as the transcript. Any failure (missing key, network error, non-200, empty response) raises `CloudError`; the caller falls back to the local Whisper transcriber for that dictation.
- **Cleanup**: `POST https://api.groq.com/openai/v1/chat/completions` with `{"model": cfg.cloud_llm_model, "temperature": 0.2, "messages": [{"role": "user", "content": <cleaner.build_rewrite_prompt(...) output, same as Ollama>}]}`, same auth header. 20 s timeout. `.choices[0].message.content` used as the rewritten text, subject to the same sanity check as the Ollama path. Any failure raises `CloudError`; the caller keeps the stage-1 (rule-based) text — it does NOT fall back to Ollama. Never called when `clean_llm` is false.
- **Connection check**: `GET https://api.groq.com/openai/v1/models` with the same auth header, 10 s timeout — used by the Settings "Test connection" button only.
