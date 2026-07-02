# Data Model: FlowLocal

## Config (persisted, `%APPDATA%\FlowLocal\config.json`)

| Field | Type | Default | Notes |
|---|---|---|---|
| `trigger` | str | `"mouse:x2"` | Tagged binding: `mouse:x1`, `mouse:x2`, or `key:<combo>` |
| `mode` | str | `"hold"` | `hold` (push-to-talk) or `toggle` |
| `mic_device` | int \| null | null | sounddevice index; null = system default |
| `model` | str | `"large-v3-turbo"` | one of `large-v3-turbo`, `distil-large-v3`, `small` |
| `language` | str \| null | null | null = auto-detect |
| `clean_fillers` | bool | true | stage-1 rules |
| `clean_llm` | bool | true | stage-2 Ollama rewrite (no-ops if Ollama absent) |
| `ollama_model` | str | `"qwen2.5:7b-instruct"` | |
| `filler_words` | list[str] | standard list | user-editable |
| `autostart` | bool | true | mirrors registry state |
| `sounds` | bool | true | start/stop cues |
| `max_record_seconds` | int | 300 | hard stop safeguard |

Validation: unknown keys ignored on load; invalid values reset to default (never crash on bad config). Writes are atomic (temp file + replace).

## DictationSession (in-memory, one per trigger cycle)

States: `RECORDING → TRANSCRIBING → CLEANING → INJECTING → DONE | FAILED`

| Field | Type |
|---|---|
| `audio` | np.ndarray (float32 mono 16 kHz) |
| `raw_text` | str |
| `clean_text` | str |
| `error` | str \| null |

Sessions are processed strictly serially by the single pipeline worker (queue depth 1; a new trigger press during processing is rejected with an error cue).
