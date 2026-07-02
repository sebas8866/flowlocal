# Feature Specification: FlowLocal — Local Voice Dictation (Wispr Flow Clone)

**Feature Directory**: `specs/001-flowlocal-dictation`
**Created**: 2026-07-02
**Status**: Draft
**Input**: Fully offline, free voice dictation app for Windows that replaces Wispr Flow — push-to-talk via any hotkey including mouse side buttons, high-accuracy local speech-to-text, Wispr-style cleanup (fillers, grammar, repetitions), types result into the focused app, tray app with settings, auto-starts with Windows.

## User Scenarios & Testing

### Primary User Story

The user works across many apps (browser, editors, chat). At any moment they press and hold a side button on their mouse, speak naturally — with filler words, restarts, and self-corrections — release the button, and within a couple of seconds clean, well-punctuated text appears in whatever text field had focus, as if they had typed it. The app runs silently in the system tray, starts with Windows, never contacts the internet, and never asks for money.

### Acceptance Scenarios

1. **Given** the app is running in the tray, **When** the user holds the bound mouse side button, speaks "um so like, send send the invoice to to John tomorrow", and releases, **Then** the focused text field receives text like "Send the invoice to John tomorrow." within a few seconds.
2. **Given** the user speaks a sentence, restarts it, and says a better version ("Let's meet at— actually, let's meet Thursday at 3"), **When** transcription completes, **Then** only the best final version appears ("Let's meet Thursday at 3.") without the abandoned fragment.
3. **Given** the machine has just booted and the user logged in, **When** the user presses the hotkey without launching anything manually, **Then** dictation works because the app auto-started.
4. **Given** the user opens the settings window from the tray icon, **When** they select a different microphone, rebind the trigger to another key or mouse button, or toggle cleanup features, **Then** the change takes effect immediately and persists across restarts.
5. **Given** the network is fully disconnected, **When** the user dictates, **Then** transcription and cleanup work identically (no cloud dependency).
6. **Given** the clipboard contains user data, **When** dictated text is injected, **Then** the user's prior clipboard content is restored afterward.

### Edge Cases

- Recording triggered with no speech (accidental press) → nothing is typed; no garbage output.
- Very long dictation (60+ seconds) → still transcribes; no crash or truncation without warning.
- Focus is on a non-text surface (desktop, image) → paste is harmless; no crash.
- Two rapid consecutive dictations → second waits for or queues behind the first; no interleaved text.
- Microphone unplugged / changed → clear tray notification, settings show remaining devices, app does not crash.
- Model files missing on first run → guided one-time download with progress indication; afterwards fully offline.
- Hotkey conflicts with another app → user can rebind from settings.

## Requirements

### Functional Requirements

- **FR-001**: System MUST capture audio from a user-selected microphone while the bound trigger is held (push-to-talk), and stop when released. A toggle mode (press to start, press to stop) MUST also be available.
- **FR-002**: Trigger MUST be bindable to keyboard keys/combos AND mouse extra buttons (side buttons XButton1/XButton2), captured globally regardless of which app has focus.
- **FR-003**: System MUST transcribe speech locally with a high-accuracy free model; no audio or text ever leaves the machine.
- **FR-004**: System MUST post-process the raw transcript: remove filler words (um, uh, like, you know), fix punctuation/capitalization/grammar, and collapse self-repetitions and false starts into the single best sentence. Each cleanup behavior MUST be individually toggleable.
- **FR-005**: System MUST inject the final text into the currently focused application at the cursor position, preserving and restoring the user's clipboard.
- **FR-006**: System MUST run as a system tray application with states visible at a glance (idle / recording / transcribing) and a menu: Settings, Pause, Quit.
- **FR-007**: Settings UI MUST allow: microphone selection, trigger rebinding (keyboard or mouse), model selection (accuracy vs speed presets), language (auto-detect default), cleanup toggles, autostart toggle, push-to-talk vs toggle mode.
- **FR-008**: System MUST support auto-start at Windows login, on by default, toggleable.
- **FR-009**: All settings MUST persist across restarts.
- **FR-010**: System MUST give audible or visual feedback when recording starts and stops.
- **FR-011**: First-run experience MUST download the chosen model once with progress shown; all subsequent operation is offline.
- **FR-012**: System MUST handle errors gracefully (no mic, model load failure, injection failure) with tray notifications, never silent data loss of a completed transcription — on injection failure the text MUST remain on the clipboard as fallback.

### Key Entities

- **Config**: persisted user preferences (mic device, trigger binding, mode, model, language, cleanup toggles, autostart).
- **Dictation session**: one press-to-release cycle — audio buffer → raw transcript → cleaned text → injection result.

## Success Criteria

- **SC-001**: From button release to text appearing: ≤ 3 seconds for a 10-second utterance on this machine (RTX 5060), ≤ 6 seconds on CPU fallback.
- **SC-002**: Dictating a natural 30-word sentence with 3+ fillers and one restart yields text requiring zero manual corrections in ≥ 8 of 10 attempts.
- **SC-003**: App survives a full reboot cycle: auto-starts and first dictation works with no manual steps.
- **SC-004**: Zero network requests during dictation (verifiable with the network disabled).
- **SC-005**: Total cost to user: $0 — no subscription, no API keys, no usage limits.
- **SC-006**: Works in any app that accepts text input (browser, VS Code, Slack, Notepad).

## Assumptions

- Single user, single machine (Windows 11, RTX 5060 GPU, Python available); no installer/distribution needed beyond this machine — a scripted local install is acceptable.
- English is the primary dictation language; multilingual support is a bonus via model choice, not a hard requirement.
- Cleanup quality target is "Wispr Flow-like", achieved locally; a lightweight local method is acceptable if it meets SC-002 (no cloud LLM).
- Clipboard-paste injection (Ctrl+V simulation with clipboard restore) is an acceptable injection mechanism, as it is the same approach Wispr Flow and similar tools use.
- "Free" means free models and open-source components only.
