# Contract: config.json

Location: `%APPDATA%\FlowLocal\config.json` — the only persisted interface. Example:

```json
{
  "trigger": "mouse:x2",
  "mode": "hold",
  "mic_device": null,
  "model": "large-v3-turbo",
  "language": null,
  "clean_fillers": true,
  "clean_llm": true,
  "ollama_model": "qwen2.5:7b-instruct",
  "filler_words": ["um", "uh", "uhm", "er", "ah", "like", "you know", "i mean", "sort of", "kind of"],
  "autostart": true,
  "sounds": true,
  "max_record_seconds": 300,
  "show_overlay": true
}
```

Guarantees:
- App never crashes on malformed/missing config — bad fields revert to defaults and file is rewritten.
- Hand-edits are picked up on next app start (settings UI edits apply immediately).
- `trigger` grammar: `mouse:x1 | mouse:x2 | key:<key>[+<key>...]` where keys use pynput canonical names (e.g. `key:ctrl_l+space`, `key:f9`).

# Contract: Ollama cleanup call (optional external interface)

`POST http://127.0.0.1:11434/api/generate` with `{"model": cfg.ollama_model, "prompt": <rewrite prompt + transcript>, "stream": false, "options": {"temperature": 0.2}}` → response `.response` used verbatim as cleaned text. 30 s timeout; any failure ⇒ fall back to stage-1 text. Never called when `clean_llm` is false.
