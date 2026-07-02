# Quickstart & Validation: FlowLocal

## Setup (one time)

```powershell
cd C:\Users\sebas\Documents\FlowLocal
.\setup.ps1          # creates .venv (py -3.11), installs deps, downloads model, smoke-tests GPU, enables autostart
```

Optional cleanup upgrade: install Ollama (https://ollama.com/download) and run `ollama pull qwen2.5:7b-instruct`. FlowLocal auto-detects it.

## Run

```powershell
.venv\Scripts\pythonw.exe run_flowlocal.pyw    # or just reboot — autostart is on
```

## Validation scenarios (map to spec acceptance scenarios)

1. **Basic dictation**: focus Notepad, hold mouse side button (forward/X2), say "um so like, send send the invoice to to John tomorrow", release → expect "Send the invoice to John tomorrow." (AS-1)
2. **Restart collapse** (needs Ollama): dictate "Let's meet at— actually, let's meet Thursday at 3" → expect "Let's meet Thursday at 3." (AS-2)
3. **Reboot**: restart Windows, log in, dictate without launching anything (AS-3)
4. **Settings**: tray icon → Settings; change mic, rebind trigger to F9, toggle LLM cleanup; confirm immediate effect + persistence after quit/relaunch (AS-4)
5. **Offline**: disable Wi-Fi/Ethernet, dictate → identical behavior (AS-5, SC-004)
6. **Clipboard restore**: copy "SENTINEL", dictate, paste manually afterwards → "SENTINEL" is back (AS-6)
7. **Accidental press**: tap trigger silently for <1 s → nothing typed (edge case, VAD)
8. **Latency**: 10 s utterance → text within ~3 s on GPU (SC-001; time it)
