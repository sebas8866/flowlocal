"""faster-whisper wrapper with lazy load and CUDA -> CPU auto-fallback.

All heavy imports (faster_whisper, numpy) happen inside methods so this
module can be imported on a bare interpreter without those packages.
"""
from __future__ import annotations

import glob
import logging
import os
import sys
import threading
import time

logger = logging.getLogger(__name__)

# Beam size used for GPU transcribe calls. CPU stays at beam 1.
BEAM_SIZE = 2

# Maps friendly/preset model names to the faster-whisper model id to try
# first. If that fails, a Hugging Face CT2 mirror is tried as a fallback.
_MODEL_ALIASES = {
    "large-v3-turbo": {
        "primary": "large-v3-turbo",
        "fallback": "deepdml/faster-whisper-large-v3-turbo-ct2",
    },
    "distil-large-v3": {
        "primary": "distil-large-v3",
        "fallback": None,
    },
    "small": {
        "primary": "small",
        "fallback": None,
    },
}

_dll_dirs_added = False
_dll_dirs_lock = threading.Lock()


def _add_nvidia_dll_dirs() -> None:
    """Locate nvidia cublas/cudnn DLL dirs bundled in the venv's
    site-packages and register them via os.add_dll_directory so CTranslate2
    can find the CUDA runtime DLLs on Windows.
    """
    global _dll_dirs_added
    with _dll_dirs_lock:
        if _dll_dirs_added:
            return
        _dll_dirs_added = True

        if not hasattr(os, "add_dll_directory"):
            return

        try:
            site_packages_dirs = set()
            for path in sys.path:
                if path and os.path.isdir(path) and os.path.basename(path) == "site-packages":
                    site_packages_dirs.add(path)
            # Also check the running interpreter's own site-packages.
            import site

            for path in site.getsitepackages() if hasattr(site, "getsitepackages") else []:
                site_packages_dirs.add(path)

            for sp in site_packages_dirs:
                nvidia_root = os.path.join(sp, "nvidia")
                if not os.path.isdir(nvidia_root):
                    continue
                for bin_dir in glob.glob(os.path.join(nvidia_root, "*", "bin")):
                    if os.path.isdir(bin_dir):
                        try:
                            os.add_dll_directory(bin_dir)
                            logger.debug("Added DLL directory: %s", bin_dir)
                        except OSError as exc:
                            logger.debug("Could not add DLL directory %s: %s", bin_dir, exc)
        except Exception as exc:
            logger.debug("Failed to scan for nvidia DLL directories: %s", exc)


class Transcriber:
    """Lazy-loading faster-whisper model with CUDA -> CPU fallback."""

    def __init__(self, model_name: str = "large-v3-turbo") -> None:
        self.model_name = model_name
        self._model = None
        self._device = None
        self._compute_type = None
        self._forced_cpu = False
        self._lock = threading.Lock()
        # Set when a reload() call fails to load any candidate; subsequent
        # transcribe() calls raise immediately instead of silently retrying
        # an online load.
        self._reload_failed = False
        # Optional callback invoked with a human-readable status string
        # (e.g. "Downloading model '...'", "Model '...' ready (GPU)").
        # Exception-guarded by the caller; never required.
        self.on_status = None

    def _notify_status(self, msg: str) -> None:
        if self.on_status is None:
            return
        try:
            self.on_status(msg)
        except Exception:
            # A broken status callback must never break model loading.
            pass

    @property
    def device(self):
        return self._device

    def reload(self, model_name: str) -> None:
        """Switch to a different model and immediately load + warm it, so
        it's ready by the time the user next dictates instead of lazily
        loading (with online HF checks) on the next transcribe() call.
        """
        with self._lock:
            self.model_name = model_name
            self._free_model_locked()
            self._device = None
            self._compute_type = None
            self._forced_cpu = False
            try:
                self._ensure_loaded()
            except Exception as exc:
                self._reload_failed = True
                logger.error("Model reload failed for '%s': %s", model_name, exc)
                raise
            self._reload_failed = False

        self.warmup()

    def release(self) -> None:
        """Free the loaded model (if any) so its GPU/CPU memory can be
        reclaimed, e.g. when switching to the cloud backend. A later
        transcribe() call lazily reloads the model as usual; this does not
        trip `_reload_failed`.
        """
        with self._lock:
            self._free_model_locked()

    def _free_model_locked(self) -> None:
        """Drop the current model reference and force a GC pass so CUDA/CPU
        memory is released before a replacement model is constructed.
        Caller must hold self._lock.
        """
        if self._model is not None:
            self._model = None
            import gc

            gc.collect()

    def _resolve_model_id(self):
        alias = _MODEL_ALIASES.get(self.model_name)
        if alias is None:
            return [self.model_name]
        candidates = [alias["primary"]]
        if alias.get("fallback"):
            candidates.append(alias["fallback"])
        return candidates

    def _load_model(self):
        _add_nvidia_dll_dirs()
        from faster_whisper import WhisperModel

        candidates = self._resolve_model_id()

        attempts = []
        if self._forced_cpu:
            attempts.append(("cpu", "int8"))
        else:
            attempts.append(("cuda", "float16"))
            attempts.append(("cpu", "int8"))

        last_exc = None
        for device, compute_type in attempts:
            for model_id in candidates:
                # Try the local HF cache first (zero network, no online
                # revision check); only fall back to a network-enabled load
                # if the model isn't cached locally yet.
                for local_files_only in (True, False):
                    if local_files_only is False:
                        # The local-cache-only attempt just failed, so this
                        # load is about to hit the network — likely a
                        # first-time, one-time download.
                        self._notify_status(
                            f"Downloading model '{model_id}' (~1.6 GB, one-time)…"
                        )
                    try:
                        model = WhisperModel(
                            model_id,
                            device=device,
                            compute_type=compute_type,
                            local_files_only=local_files_only,
                        )
                        self._model = model
                        self._device = device
                        self._compute_type = compute_type
                        if device == "cpu":
                            self._forced_cpu = True
                        logger.info(
                            "Loaded whisper model '%s' on device=%s compute_type=%s "
                            "local_files_only=%s",
                            model_id, device, compute_type, local_files_only,
                        )
                        device_label = "GPU" if device == "cuda" else "CPU"
                        self._notify_status(
                            f"Model '{model_id}' ready ({device_label})"
                        )
                        return
                    except Exception as exc:
                        logger.warning(
                            "Failed to load model '%s' on device=%s (%s) "
                            "local_files_only=%s: %s",
                            model_id, device, compute_type, local_files_only, exc,
                        )
                        last_exc = exc
                        continue

        raise RuntimeError(f"Could not load any whisper model candidate") from last_exc

    def _ensure_loaded(self):
        if self._model is None:
            self._load_model()

    def warmup(self) -> None:
        """Force the model to load now (not lazily) and run one real dummy
        inference that cannot early-exit on silence, so CUDA kernels/JIT
        and any first-call overhead are paid before the user's first real
        dictation.
        """
        start = time.monotonic()
        try:
            import numpy as np

            with self._lock:
                self._ensure_loaded()

                dummy_audio = (
                    np.random.default_rng(0).standard_normal(16000).astype("float32") * 0.01
                )
                segments, _info = self._model.transcribe(
                    dummy_audio,
                    vad_filter=False,
                )
                list(segments)
        except Exception as exc:
            logger.warning("Transcriber warmup failed: %s", exc)
            return

        logger.info("Transcriber warmup completed in %.2fs", time.monotonic() - start)

    def transcribe(self, audio_np, language: str = None) -> str:
        """Transcribe a mono 16kHz float32 numpy array. Returns "" for
        empty/silent audio.
        """
        import numpy as np

        if audio_np is None or len(audio_np) == 0:
            return ""

        if float(np.abs(audio_np).max()) < 1e-4:
            return ""

        start = time.monotonic()
        audio_seconds = len(audio_np) / 16000.0

        with self._lock:
            if self._reload_failed:
                raise RuntimeError(
                    "Transcriber model reload previously failed; restart FlowLocal "
                    "or select a different model to recover."
                )

            self._ensure_loaded()

            beam_size = 1 if self._device == "cpu" else BEAM_SIZE

            try:
                segments, _info = self._model.transcribe(
                    audio_np,
                    language=language,
                    vad_filter=True,
                    beam_size=beam_size,
                    condition_on_previous_text=False,
                )
                texts = [seg.text.strip() for seg in segments]
                result = " ".join(t for t in texts if t).strip()
            except Exception as exc:
                if self._forced_cpu:
                    logger.error("Transcription failed even on CPU fallback: %s", exc)
                    raise
                logger.warning(
                    "Transcription failed on device=%s (%s); falling back to CPU int8: %s",
                    self._device, self._compute_type, exc,
                )
                self._forced_cpu = True
                self._free_model_locked()
                self._ensure_loaded()
                beam_size = 1
                segments, _info = self._model.transcribe(
                    audio_np,
                    language=language,
                    vad_filter=True,
                    beam_size=beam_size,
                    condition_on_previous_text=False,
                )
                texts = [seg.text.strip() for seg in segments]
                result = " ".join(t for t in texts if t).strip()

        logger.info(
            "Transcribed %.2fs audio in %.2fs",
            audio_seconds, time.monotonic() - start,
        )
        return result
