"""Config dataclass with JSON persistence at %APPDATA%\\FlowLocal\\config.json.

Pure stdlib module (json, os, dataclasses) — safe to import anywhere,
including in the unit-test environment with no third-party packages.
"""
from __future__ import annotations

import json
import os
import logging
from dataclasses import dataclass, field, fields, asdict
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_FILLER_WORDS = [
    "um", "uh", "uhm", "er", "ah", "like", "you know", "i mean", "sort of", "kind of",
]

VALID_MODELS = {"large-v3-turbo", "distil-large-v3", "small"}
VALID_MODES = {"hold", "toggle"}
VALID_BACKENDS = {"local", "cloud"}


def _app_data_dir() -> str:
    """Return %APPDATA%\\FlowLocal, creating it if necessary."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "FlowLocal")
    os.makedirs(path, exist_ok=True)
    return path


def config_path() -> str:
    return os.path.join(_app_data_dir(), "config.json")


def _default_filler_words() -> list:
    return list(DEFAULT_FILLER_WORDS)


@dataclass
class Config:
    trigger: str = "mouse:x2"
    mode: str = "hold"
    mic_device: Optional[int] = None
    mic_device_name: Optional[str] = None
    model: str = "large-v3-turbo"
    language: Optional[str] = None
    clean_fillers: bool = True
    clean_llm: bool = True
    ollama_model: str = "qwen2.5:7b-instruct"
    filler_words: list = field(default_factory=_default_filler_words)
    autostart: bool = True
    sounds: bool = True
    max_record_seconds: int = 300
    show_overlay: bool = True
    backend: str = "local"
    groq_api_key: str = ""
    cloud_stt_model: str = "whisper-large-v3-turbo"
    cloud_llm_model: str = "llama-3.3-70b-versatile"

    def _validate(self) -> None:
        """Reset any field holding an invalid value back to its default."""
        defaults = Config.__dataclass_fields__

        if not isinstance(self.trigger, str) or not self.trigger:
            self.trigger = defaults["trigger"].default

        if self.mode not in VALID_MODES:
            self.mode = defaults["mode"].default

        if self.mic_device is not None and not isinstance(self.mic_device, int):
            self.mic_device = None

        if self.mic_device_name is not None and not isinstance(self.mic_device_name, str):
            self.mic_device_name = None

        if self.model not in VALID_MODELS:
            self.model = defaults["model"].default

        if self.language is not None and not isinstance(self.language, str):
            self.language = None

        if not isinstance(self.clean_fillers, bool):
            self.clean_fillers = defaults["clean_fillers"].default

        if not isinstance(self.clean_llm, bool):
            self.clean_llm = defaults["clean_llm"].default

        if not isinstance(self.ollama_model, str) or not self.ollama_model:
            self.ollama_model = defaults["ollama_model"].default

        if not isinstance(self.filler_words, list) or not all(
            isinstance(w, str) for w in self.filler_words
        ):
            self.filler_words = _default_filler_words()

        if not isinstance(self.autostart, bool):
            self.autostart = defaults["autostart"].default

        if not isinstance(self.sounds, bool):
            self.sounds = defaults["sounds"].default

        if not isinstance(self.max_record_seconds, int) or isinstance(
            self.max_record_seconds, bool
        ) or self.max_record_seconds <= 0:
            self.max_record_seconds = defaults["max_record_seconds"].default

        if not isinstance(self.show_overlay, bool):
            self.show_overlay = defaults["show_overlay"].default

        if self.backend not in VALID_BACKENDS:
            self.backend = defaults["backend"].default

        if not isinstance(self.groq_api_key, str):
            self.groq_api_key = defaults["groq_api_key"].default

        if not isinstance(self.cloud_stt_model, str) or not self.cloud_stt_model:
            self.cloud_stt_model = defaults["cloud_stt_model"].default

        if not isinstance(self.cloud_llm_model, str) or not self.cloud_llm_model:
            self.cloud_llm_model = defaults["cloud_llm_model"].default

    @classmethod
    def load(cls) -> "Config":
        """Load config from disk, tolerating a missing or corrupt file.

        Unknown keys are ignored. Invalid values are reset to defaults.
        Never raises.
        """
        path = config_path()
        data = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                valid_keys = {f.name for f in fields(cls)}
                data = {k: v for k, v in raw.items() if k in valid_keys}
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("Failed to read config (%s); using defaults", exc)
            data = {}

        try:
            cfg = cls(**data)
        except TypeError as exc:
            logger.warning("Malformed config data (%s); using defaults", exc)
            cfg = cls()

        cfg._validate()
        return cfg

    def save(self) -> None:
        """Atomically write config to disk (temp file + os.replace)."""
        self._validate()
        path = config_path()
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp_path, path)
        except OSError as exc:
            logger.error("Failed to save config: %s", exc)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
