"""Global trigger management: keyboard + mouse listeners via pynput.

Binding grammar (see contracts/config-schema.md):
    mouse:x1 | mouse:x2 | key:<key>[+<key>...]
where keys use pynput canonical names, e.g. "key:ctrl_l+space", "key:f9".

pynput is imported lazily inside methods so this module can be imported
without it installed.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Any held key older than this is considered stuck (a release event was
# missed, e.g. focus-stealing by another app) and is pruned so it can't
# permanently wedge a combo trigger.
_STUCK_KEY_TIMEOUT_SECONDS = 30.0

# Windows raw mouse message codes for the X-button (side button) events,
# used by the win32_event_filter on the mouse listener to identify which
# low-level message is arriving (pynput passes these through as `msg`).
_WM_XBUTTONDOWN = 0x020B
_WM_XBUTTONUP = 0x020C

# Bare-modifier key names that must never be captured alone as a binding
# (see capture guard, #7): waiting continues until a non-modifier key or a
# mouse x-button arrives.
_MODIFIER_KEY_NAMES = {
    "ctrl", "ctrl_l", "ctrl_r",
    "shift", "shift_l", "shift_r",
    "alt", "alt_l", "alt_r", "alt_gr",
    "cmd", "cmd_l", "cmd_r",
}

# Windows virtual-key codes for modifier keys, used by the keyboard
# win32_event_filter to recognize (and never suppress-capture) a bare
# modifier press while in capture mode.
_MODIFIER_KEY_VKS = {
    0xA2, 0xA3, 0x11,  # VK_LCONTROL, VK_RCONTROL, VK_CONTROL
    0xA0, 0xA1, 0x10,  # VK_LSHIFT, VK_RSHIFT, VK_SHIFT
    0xA4, 0xA5, 0x12,  # VK_LMENU, VK_RMENU, VK_MENU (alt)
    0x5B, 0x5C,        # VK_LWIN, VK_RWIN
}

# Canonical pynput key name -> Windows virtual-key code, for the small set
# of named (non-character) keys that are practical to bind (function keys,
# esc, space, etc). Used to resolve which vk(s) a "key:" binding covers so
# the event filter can suppress the matching low-level event.
_NAMED_KEY_VKS = {
    "esc": 0x1B,
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "page_up": 0x21,
    "page_down": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "ctrl_l": 0xA2,
    "ctrl_r": 0xA3,
    "ctrl": 0x11,
    "shift_l": 0xA0,
    "shift_r": 0xA1,
    "shift": 0x10,
    "alt_l": 0xA4,
    "alt_r": 0xA5,
    "alt_gr": 0xA5,
    "alt": 0x12,
    "cmd_l": 0x5B,
    "cmd_r": 0x5C,
    "cmd": 0x5B,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}


def _key_to_name(key) -> Optional[str]:
    """Normalize a pynput Key/KeyCode to a canonical lowercase name."""
    from pynput import keyboard

    if isinstance(key, keyboard.KeyCode):
        if key.char:
            return key.char.lower()
        if key.vk is not None:
            return f"vk{key.vk}"
        return None
    if isinstance(key, keyboard.Key):
        return key.name
    return None


def parse_binding(binding: str) -> dict:
    """Parse a binding string into a structured dict:
    {"type": "mouse", "button": "x1"} or {"type": "key", "keys": frozenset(...)}
    """
    if not binding or ":" not in binding:
        raise ValueError(f"Invalid binding: {binding!r}")

    kind, _, rest = binding.partition(":")
    kind = kind.strip().lower()
    rest = rest.strip().lower()

    if kind == "mouse":
        if rest not in ("x1", "x2"):
            raise ValueError(f"Invalid mouse binding: {binding!r}")
        return {"type": "mouse", "button": rest}

    if kind == "key":
        keys = frozenset(k.strip() for k in rest.split("+") if k.strip())
        if not keys:
            raise ValueError(f"Invalid key binding: {binding!r}")
        return {"type": "key", "keys": keys}

    raise ValueError(f"Invalid binding kind: {binding!r}")


def format_binding(kind: str, value) -> str:
    """Format a parsed binding back into its string form."""
    if kind == "mouse":
        return f"mouse:{value}"
    if kind == "key":
        keys = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
        return "key:" + "+".join(sorted(keys))
    raise ValueError(f"Invalid binding kind: {kind!r}")


class TriggerManager:
    """Runs global keyboard + mouse listeners and fires on_press/on_release
    (hold mode) or a single toggle callback (toggle mode) when the bound
    trigger fires.
    """

    def __init__(
        self,
        binding: str,
        mode: str = "hold",
        on_press: Optional[Callable[[], None]] = None,
        on_release: Optional[Callable[[], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
    ) -> None:
        self._lock = threading.Lock()
        self.mode = mode
        self.on_press = on_press
        self.on_release = on_release
        self.on_cancel = on_cancel

        self._binding_spec = parse_binding(binding)
        self._binding_str = binding

        self._held_keys: Set[str] = set()
        self._held_key_times: Dict[str, float] = {}
        self._trigger_active = False
        self._toggle_state = False

        self._keyboard_listener = None
        self._mouse_listener = None

        self._capture_mode = False
        self._capture_callback: Optional[Callable[[str], None]] = None

        # When True (the default), events that match the current binding
        # are swallowed via pynput's win32_event_filter/suppress_event() so
        # the focused app underneath never sees them (e.g. mouse X2 no
        # longer triggers browser "Forward"). Also suppressed during
        # capture mode so the captured press doesn't leak either; the flag
        # itself is reserved for a future settings toggle.
        self.suppress_enabled = True

    @property
    def binding(self) -> str:
        return self._binding_str

    def set_binding(self, binding: str) -> None:
        with self._lock:
            self._binding_spec = parse_binding(binding)
            self._binding_str = binding
            self._held_keys.clear()
            self._held_key_times.clear()
            self._trigger_active = False
            self._toggle_state = False

    def set_mode(self, mode: str) -> None:
        with self._lock:
            self.mode = mode
            self._trigger_active = False
            self._toggle_state = False

    def start(self) -> None:
        from pynput import keyboard, mouse

        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
            win32_event_filter=self._keyboard_event_filter,
        )
        self._mouse_listener = mouse.Listener(
            on_click=self._on_click,
            win32_event_filter=self._mouse_event_filter,
        )
        self._keyboard_listener.daemon = True
        self._mouse_listener.daemon = True
        self._keyboard_listener.start()
        self._mouse_listener.start()

    def stop(self) -> None:
        if self._keyboard_listener is not None:
            self._keyboard_listener.stop()
            self._keyboard_listener = None
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
            self._mouse_listener = None

    def capture_next(self, callback: Callable[[str], None]) -> None:
        """One-shot mode: the next key press or mouse x-button press is
        captured and its binding string passed to `callback`. Normal
        trigger handling is suppressed while capturing.
        """
        with self._lock:
            self._capture_mode = True
            self._capture_callback = callback

    def cancel_capture(self) -> None:
        with self._lock:
            self._capture_mode = False
            self._capture_callback = None

    # --- win32 event filters (run before on_press/on_release/on_click) -----
    #
    # These run on the hook thread BEFORE the normal on_press/on_release/
    # on_click callbacks. Calling listener.suppress_event() inside a filter
    # stops the event from propagating to the focused app underneath, but
    # our own on_press/on_release/on_click callbacks still fire normally
    # afterward (suppression only affects the OS-level propagation, not
    # pynput's own listener callbacks). We only ever suppress events that
    # match the current binding (or, during capture, the press being
    # captured) — everything else must pass through untouched.

    def _keyboard_event_filter(self, msg, data) -> None:
        if not self.suppress_enabled:
            return

        with self._lock:
            capturing = self._capture_mode
            spec = self._binding_spec
            listener = self._keyboard_listener

        if listener is None:
            return

        vk = getattr(data, "vk", None)
        if vk is None:
            return

        if capturing:
            # Any non-modifier key press is about to be captured as the new
            # binding; suppress it too so it doesn't leak to the focused
            # app. Modifier-only presses fall through untouched (they are
            # ignored by the capture logic and never leak anything
            # dictation-related).
            if vk in _MODIFIER_KEY_VKS:
                return
            listener.suppress_event()
            return

        if spec["type"] != "key":
            return

        # Only suppress when this vk corresponds to one of the bound keys
        # AND matches a key name actually present in the binding. We can't
        # resolve vk -> canonical name without constructing a KeyCode, so
        # compare against the set of vks for the current binding (computed
        # lazily/cached on the spec).
        bound_vks = self._bound_key_vks()
        if bound_vks and vk in bound_vks:
            listener.suppress_event()

    def _mouse_event_filter(self, msg, data) -> None:
        if not self.suppress_enabled:
            return

        with self._lock:
            capturing = self._capture_mode
            spec = self._binding_spec
            listener = self._mouse_listener

        if listener is None:
            return

        if msg not in (_WM_XBUTTONDOWN, _WM_XBUTTONUP):
            return

        mouse_data = getattr(data, "mouseData", None)
        if mouse_data is None:
            return
        # High word of mouseData identifies which X button (XBUTTON1=1,
        # XBUTTON2=2); Windows stores it signed/unsigned depending on
        # struct packing, so mask defensively.
        hiword = (mouse_data >> 16) & 0xFFFF
        if hiword == 1:
            name = "x1"
        elif hiword == 2:
            name = "x2"
        else:
            return

        if capturing:
            listener.suppress_event()
            return

        if spec["type"] == "mouse" and spec["button"] == name:
            listener.suppress_event()

    def _bound_key_vks(self):
        """Return the set of virtual-key codes for the current key binding,
        recomputed lazily from the held-key vk map. Caller need not hold
        self._lock (spec is read atomically via the reference).
        """
        spec = self._binding_spec
        if spec["type"] != "key":
            return frozenset()
        vks = set()
        for name in spec["keys"]:
            if name.startswith("vk") and name[2:].isdigit():
                vks.add(int(name[2:]))
            else:
                vk = _NAMED_KEY_VKS.get(name)
                if vk is not None:
                    vks.add(vk)
        return vks

    # --- keyboard events ---------------------------------------------------

    def _prune_stuck_keys(self) -> None:
        """Drop any held key whose press timestamp is older than the stuck
        threshold. Caller must hold self._lock.
        """
        if not self._held_key_times:
            return
        now = time.monotonic()
        stuck = [
            k for k, t in self._held_key_times.items()
            if now - t >= _STUCK_KEY_TIMEOUT_SECONDS
        ]
        for k in stuck:
            self._held_keys.discard(k)
            self._held_key_times.pop(k, None)
        if stuck:
            logger.debug("Pruned stuck held keys (missed release?): %s", stuck)

    def _on_key_press(self, key) -> None:
        name = _key_to_name(key)
        if name is None:
            return

        cancel_cb = None

        with self._lock:
            if self._capture_mode:
                # Ignore bare modifier presses so users can't accidentally
                # bind e.g. "ctrl_l" alone — keep waiting for a real key.
                if name in _MODIFIER_KEY_NAMES:
                    return
                binding = format_binding("key", [name])
                cb = self._capture_callback
                self._capture_mode = False
                self._capture_callback = None
                self._prune_stuck_keys()
                if cb:
                    cb(binding)
                return

            if name == "esc" and (
                self._trigger_active or (self.mode == "toggle" and self._toggle_state)
            ):
                # Esc cancels an in-progress recording instead of behaving
                # like a normal held key: reset all trigger state and fire
                # on_cancel outside the lock, skipping normal press handling.
                self._trigger_active = False
                self._toggle_state = False
                self._held_keys.clear()
                self._held_key_times.clear()
                cancel_cb = self.on_cancel
            else:
                self._held_keys.add(name)
                self._held_key_times[name] = time.monotonic()
                spec = self._binding_spec
                if spec["type"] != "key":
                    return
                required = spec["keys"]
                is_match = required.issubset(self._held_keys)
                mode = self.mode
                already_active = self._trigger_active
                if is_match:
                    self._trigger_active = True

        if cancel_cb is not None:
            cancel_cb()
            return

        if spec["type"] != "key" or not is_match or already_active:
            return

        self._fire_press(mode)

    def _on_key_release(self, key) -> None:
        name = _key_to_name(key)
        if name is None:
            return

        with self._lock:
            self._held_keys.discard(name)
            self._held_key_times.pop(name, None)
            spec = self._binding_spec
            if spec["type"] != "key":
                return
            required = spec["keys"]
            was_active = self._trigger_active
            # Release fires once any required key is let go.
            if was_active and not required.issubset(self._held_keys):
                self._trigger_active = False
            else:
                return
            mode = self.mode

        self._fire_release(mode)

    # --- mouse events --------------------------------------------------

    def _on_click(self, x, y, button, pressed) -> None:
        from pynput.mouse import Button

        name = None
        if button == Button.x1:
            name = "x1"
        elif button == Button.x2:
            name = "x2"
        else:
            return

        with self._lock:
            if self._capture_mode:
                if pressed:
                    binding = format_binding("mouse", name)
                    cb = self._capture_callback
                    self._capture_mode = False
                    self._capture_callback = None
                    if cb:
                        cb(binding)
                return

            spec = self._binding_spec
            if spec["type"] != "mouse" or spec["button"] != name:
                return

            mode = self.mode
            if pressed:
                if self._trigger_active:
                    return
                self._trigger_active = True
            else:
                if not self._trigger_active:
                    return
                self._trigger_active = False

        if pressed:
            self._fire_press(mode)
        else:
            self._fire_release(mode)

    # --- firing helpers --------------------------------------------------

    def _fire_press(self, mode: str) -> None:
        with self._lock:
            self._prune_stuck_keys()
        if mode == "hold":
            if self.on_press:
                self.on_press()
        elif mode == "toggle":
            with self._lock:
                currently_recording = self._toggle_state
            if not currently_recording:
                # Attempting to start a new recording: the app is
                # authoritative on whether this is accepted (e.g. rejects
                # if paused or already busy). Only flip to "recording" if
                # on_press confirms it actually started. A None return is
                # treated as accepted for backward compatibility.
                accepted = True
                if self.on_press:
                    accepted = self.on_press()
                    if accepted is None:
                        accepted = True
                if accepted:
                    with self._lock:
                        self._toggle_state = True
            else:
                with self._lock:
                    self._toggle_state = False
                if self.on_release:
                    self.on_release()

    def _fire_release(self, mode: str) -> None:
        if mode == "hold":
            if self.on_release:
                self.on_release()
        # toggle mode fires entirely on press; release is a no-op.
