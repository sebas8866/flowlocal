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
from typing import Callable, List, Optional, Set

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._lock = threading.Lock()
        self.mode = mode
        self.on_press = on_press
        self.on_release = on_release

        self._binding_spec = parse_binding(binding)
        self._binding_str = binding

        self._held_keys: Set[str] = set()
        self._trigger_active = False
        self._toggle_state = False

        self._keyboard_listener = None
        self._mouse_listener = None

        self._capture_mode = False
        self._capture_callback: Optional[Callable[[str], None]] = None

    @property
    def binding(self) -> str:
        return self._binding_str

    def set_binding(self, binding: str) -> None:
        with self._lock:
            self._binding_spec = parse_binding(binding)
            self._binding_str = binding
            self._held_keys.clear()
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
            on_press=self._on_key_press, on_release=self._on_key_release
        )
        self._mouse_listener = mouse.Listener(on_click=self._on_click)
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

    # --- keyboard events ---------------------------------------------------

    def _on_key_press(self, key) -> None:
        name = _key_to_name(key)
        if name is None:
            return

        with self._lock:
            if self._capture_mode:
                binding = format_binding("key", [name])
                cb = self._capture_callback
                self._capture_mode = False
                self._capture_callback = None
                if cb:
                    cb(binding)
                return

            self._held_keys.add(name)
            spec = self._binding_spec
            if spec["type"] != "key":
                return
            required = spec["keys"]
            is_match = required.issubset(self._held_keys)
            mode = self.mode
            already_active = self._trigger_active
            if is_match:
                self._trigger_active = True

        if spec["type"] != "key" or not is_match or already_active:
            return

        self._fire_press(mode)

    def _on_key_release(self, key) -> None:
        name = _key_to_name(key)
        if name is None:
            return

        with self._lock:
            self._held_keys.discard(name)
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
        if mode == "hold":
            if self.on_press:
                self.on_press()
        elif mode == "toggle":
            with self._lock:
                self._toggle_state = not self._toggle_state
                now_recording = self._toggle_state
            if now_recording:
                if self.on_press:
                    self.on_press()
            else:
                if self.on_release:
                    self.on_release()

    def _fire_release(self, mode: str) -> None:
        if mode == "hold":
            if self.on_release:
                self.on_release()
        # toggle mode fires entirely on press; release is a no-op.
