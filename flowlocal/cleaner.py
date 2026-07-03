"""Transcript cleanup pipeline.

STAGE 1 (this module's core, `_stage1_rules`) is pure stdlib — only `re` and
`string` — so it has zero third-party dependencies and can be unit-tested on
a bare Python interpreter with nothing installed.

STAGE 2 is an optional local-LLM rewrite via Ollama. `requests` is imported
lazily inside the functions that need it, so importing this module never
requires it.
"""
from __future__ import annotations

import re
import time
import logging

logger = logging.getLogger(__name__)

_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
_OLLAMA_GENERATE_URL = f"{_OLLAMA_BASE_URL}/api/generate"
_OLLAMA_TAGS_URL = f"{_OLLAMA_BASE_URL}/api/tags"

_FEW_SHOT_EXAMPLES = (
    (
        "meet Tuesday— actually Wednesday works better for me",
        "Wednesday works better for me.",
    ),
    (
        "um so I I need you to, like, send send the report by uh Friday",
        "So I need you to send the report by Friday.",
    ),
    (
        "my email is john dot smith at gmail dot com and call me at "
        "five five five one two three four",
        "My email is john.smith@gmail.com and call me at 555-1234.",
    ),
)


def build_rewrite_prompt(text: str, cfg, app_context=None, previous=None) -> str:
    """Build the stage-2 LLM rewrite prompt, shared by the local Ollama path
    and the cloud (Groq) path so both produce identically-styled output.
    """
    lines = [
        "You clean up voice-dictation transcripts.",
        "",
        "Rules:",
        "1. Remove filler words (um, uh, like, you know, etc.).",
        "2. When the speaker self-corrects or restarts mid-thought (e.g. "
        '"meet Tuesday— actually Wednesday"), keep ONLY the final intended '
        "version — drop the abandoned part entirely.",
        "3. Collapse stutters and immediate word/phrase repeats.",
        "4. Fix grammar and punctuation, but keep the speaker's tone and "
        "word choice — never paraphrase, formalize, shorten, or summarize.",
        "5. Preserve ALL content and details from the original.",
        "6. Format spoken constructs naturally: spoken email addresses "
        'become normal addresses (e.g. "john dot smith at gmail dot com" '
        '-> "john.smith@gmail.com"), spoken URLs become proper URLs, and '
        "quantities/dates/times are written as digits.",
        '7. If the speaker says "new line" or "new paragraph" as a '
        "dictation command (not as part of a sentence), replace it with "
        "the corresponding line break instead of the literal words.",
        "8. Output ONLY the cleaned text — no commentary, no surrounding "
        "quotes.",
        "",
        "Examples:",
    ]

    for example_in, example_out in _FEW_SHOT_EXAMPLES:
        lines.append(f"Input: {example_in}")
        lines.append(f"Output: {example_out}")

    vocabulary = getattr(cfg, "vocabulary", None)
    if vocabulary:
        joined = ", ".join(vocabulary)
        lines.append("")
        lines.append(
            f"Correct spellings of names/terms the speaker uses: {joined}"
        )

    if app_context:
        lines.append("")
        lines.append(
            f"The text will be typed into: {app_context}. Match that "
            "app's natural style (casual chat vs formal email vs "
            "technical editor) WITHOUT changing the meaning."
        )

    if previous:
        lines.append("")
        lines.append(
            "The speaker's previous dictation moments ago (context only "
            f"— do NOT repeat or rewrite it): {previous}"
        )

    lines.append("")
    lines.append(f"Transcript:\n{text}")

    return "\n".join(lines)


# Backward-compatible module-level prompt template — some tests/tools may
# still reference this constant directly. Kept in sync in spirit with
# build_rewrite_prompt's rules; new code should call build_rewrite_prompt().
REWRITE_PROMPT = (
    "You clean up voice-dictation transcripts. Rewrite the following "
    "dictated speech transcript:\n"
    "- Remove filler words (um, uh, like, you know, etc.)\n"
    "- When the speaker self-corrects or restarts, keep ONLY the final "
    "intended version\n"
    "- Collapse stutters and repeats\n"
    "- Fix grammar and punctuation, but keep the speaker's tone and word "
    "choice — never paraphrase, formalize, shorten, or summarize\n"
    "- Preserve ALL content and details\n"
    "- Format spoken emails/URLs/numbers naturally\n"
    "- Output ONLY the rewritten text, with no preamble, quotes, or "
    "explanation\n\n"
    "Transcript:\n{text}"
)
# Backward-compatible alias for existing internal references.
_REWRITE_PROMPT = REWRITE_PROMPT

# --- Stage 1: pure stdlib rule-based cleanup -------------------------------

_WORD_RE = re.compile(r"\S+")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.!?;:])")
_DUPLICATE_PUNCT_RE = re.compile(r"([,.!?;:])[ \t]*,")
_LEADING_COMMA_RE = re.compile(r"^\s*,\s*")
_SENTENCE_SPLIT_RE = re.compile(r"([.!?]+\s*)")
_TERMINAL_PUNCT_RE = re.compile(r"[.!?]$")

# Conservative "new line" / "new paragraph" dictation-command matcher: the
# phrase must read as its own clause, not as a noun phrase embedded in a
# sentence. We require:
#   - NOT immediately preceded by an article/determiner ("a", "the",
#     "another", "this", "that") — rules out "a new line of products".
#   - NOT immediately followed by a noun/preposition that would continue a
#     noun phrase ("of", "item", "items", "in", "for") — rules out "a new
#     line of products" / "a new line item".
# Otherwise bounded by start/end of string, punctuation, or a clause
# conjunction ("and", "then", "so", "but"), which covers both punctuated
# and un-punctuated raw STT transcripts.
_NO_PRECEDING_DETERMINER = r"(?<!\ba\s)(?<!\ban\s)(?<!\bthe\s)(?<!\banother\s)(?<!\bthis\s)(?<!\bthat\s)"
_NO_FOLLOWING_NOUN_CONTINUATION = r"(?!\s+(?:of|item|items|in|for|from|to)\b)"
_CLAUSE_LEFT = _NO_PRECEDING_DETERMINER + r"(?:^|(?<=[.!?,;:\s]))"
_CLAUSE_RIGHT = _NO_FOLLOWING_NOUN_CONTINUATION + r"(?:$|(?=[.!?,;:\s]))"
_NEWLINE_CMD_RE = re.compile(
    _CLAUSE_LEFT + r"\s*new line\s*" + _CLAUSE_RIGHT,
    re.IGNORECASE,
)
_PARAGRAPH_CMD_RE = re.compile(
    _CLAUSE_LEFT + r"\s*new paragraph\s*" + _CLAUSE_RIGHT,
    re.IGNORECASE,
)

_HALLUCINATION_PHRASES = {
    "thank you",
    "thanks for watching",
    "thank you for watching",
    "thank you bye",
    "bye",
    "you",
    "subtitles by the amara org community",
    "please subscribe",
    "the end",
}
_HALLUCINATION_STRIP_RE = re.compile(r"[^\w\s]")


def is_hallucination(text: str) -> bool:
    """Detect classic Whisper silence-hallucination outputs by exact match
    after lowercasing and stripping punctuation/whitespace. Conservative by
    design — near-misses (e.g. "thank you everyone for coming") must NOT
    match.
    """
    if not text:
        return False
    normalized = _HALLUCINATION_STRIP_RE.sub("", text.lower())
    normalized = " ".join(normalized.split())
    return normalized in _HALLUCINATION_PHRASES


_UNDO_PHRASES = {
    "scratch that",
    "delete that",
    "undo that",
    "undo last",
}


def is_undo_command(text: str) -> bool:
    """Whole-utterance match (lowercased, punctuation stripped) against the
    supported undo phrasings.
    """
    if not text:
        return False
    normalized = _HALLUCINATION_STRIP_RE.sub("", text.lower())
    normalized = " ".join(normalized.split())
    return normalized in _UNDO_PHRASES


def _apply_newline_commands(text: str) -> str:
    """Replace standalone "new line"/"new paragraph" dictation commands with
    actual line breaks. Conservative: only replaces when the phrase forms
    its own clause; when in doubt, leaves the text unchanged.
    """
    text = _PARAGRAPH_CMD_RE.sub("\n\n", text)
    text = _NEWLINE_CMD_RE.sub("\n", text)
    # Tidy horizontal whitespace hugging the inserted line break(s), without
    # touching the newlines themselves.
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return text


def _build_filler_pattern(filler_words):
    """Build a single case-insensitive, word-boundary regex matching any
    filler word/phrase (multi-word phrases included), longest first so
    phrases are matched before their sub-words.
    """
    words = [w.strip() for w in (filler_words or []) if w and w.strip()]
    if not words:
        return None
    # Longest phrases first to avoid partial matches shadowing full phrases.
    words = sorted(set(words), key=len, reverse=True)
    escaped = [re.escape(w) for w in words]
    pattern = r"\b(?:" + "|".join(escaped) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


def _remove_fillers(text: str, filler_words) -> str:
    pattern = _build_filler_pattern(filler_words)
    if pattern is None:
        return text
    return pattern.sub("", text)


def _dedupe_immediate_repeats(text: str) -> str:
    """Collapse immediate duplicate words, case-insensitive, up to 3 in a
    row ("send send send" -> "send").
    """
    tokens = text.split(" ")
    result = []
    for token in tokens:
        stripped = token.strip(".,!?;:").lower()
        if result:
            prev_stripped = result[-1].strip(".,!?;:").lower()
            if stripped and stripped == prev_stripped:
                continue
        result.append(token)
    return " ".join(result)


def _cleanup_punctuation_artifacts(text: str) -> str:
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    # ", ," or " ," style doubled/orphaned commas -> single punctuation
    text = _DUPLICATE_PUNCT_RE.sub(r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    text = _LEADING_COMMA_RE.sub("", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    # Strip only horizontal whitespace at the edges — a leading/trailing
    # newline can be meaningful (e.g. a "new line" voice command at the
    # very start/end of the utterance) and must survive.
    return text.strip(" \t")


def _capitalize_sentences(text: str) -> str:
    if not text:
        return text
    parts = _SENTENCE_SPLIT_RE.split(text)
    out = []
    for part in parts:
        if not part:
            continue
        stripped = part.lstrip()
        lead_ws = part[: len(part) - len(stripped)]
        if stripped and stripped[0].isalpha():
            stripped = stripped[0].upper() + stripped[1:]
        out.append(lead_ws + stripped)
    return "".join(out)


def _ensure_terminal_punctuation(text: str) -> str:
    if not text:
        return text
    if not _TERMINAL_PUNCT_RE.search(text):
        text = text + "."
    return text


def _stage1_rules(text: str, cfg) -> str:
    """Deterministic, dependency-free cleanup rules."""
    if not text or not text.strip():
        return ""

    result = text.strip()

    if getattr(cfg, "voice_commands", True):
        result = _apply_newline_commands(result)

    if getattr(cfg, "clean_fillers", True):
        filler_words = getattr(cfg, "filler_words", None)
        result = _remove_fillers(result, filler_words)

    result = _dedupe_immediate_repeats(result)
    result = _cleanup_punctuation_artifacts(result)

    if not result.strip():
        return ""

    result = _capitalize_sentences(result)
    result = _cleanup_punctuation_artifacts(result)

    if not result.strip():
        return ""

    result = _ensure_terminal_punctuation(result)
    return result


# --- Stage 2: optional local LLM rewrite via Ollama ------------------------

_ollama_status_cache = {"checked_at": 0.0, "available": False}
_OLLAMA_CACHE_TTL_SECONDS = 60.0


def ollama_available() -> bool:
    """Probe GET /api/tags with a 1s timeout; cache result for 60s."""
    now = time.time()
    if now - _ollama_status_cache["checked_at"] < _OLLAMA_CACHE_TTL_SECONDS:
        return _ollama_status_cache["available"]

    available = False
    try:
        import requests

        resp = requests.get(_OLLAMA_TAGS_URL, timeout=1)
        available = resp.status_code == 200
    except Exception as exc:
        logger.debug("Ollama not available: %s", exc)
        available = False

    _ollama_status_cache["checked_at"] = now
    _ollama_status_cache["available"] = available
    return available


def sanity_check(original: str, rewritten: str) -> bool:
    """Reject empty or implausibly-sized LLM rewrites. Shared by the Ollama
    and Groq cleanup paths so both apply the same output-sanity guard.
    """
    if not rewritten or not rewritten.strip():
        return False
    orig_len = len(original.strip())
    new_len = len(rewritten.strip())
    if orig_len == 0:
        return False
    return (orig_len * 0.3) <= new_len <= (orig_len * 3.0)


# Backward-compatible alias for existing internal references.
_sanity_check = sanity_check


def _stage2_llm_rewrite(
    text: str, cfg, stage1_result: str, app_context=None, previous=None
) -> str:
    """POST to Ollama for a grammar/false-start rewrite; fall back to the
    stage-1 result on any failure or implausible output.
    """
    if not text or not text.strip():
        return stage1_result

    try:
        import requests

        prompt = build_rewrite_prompt(text, cfg, app_context=app_context, previous=previous)
        payload = {
            "model": getattr(cfg, "ollama_model", "qwen2.5:7b-instruct"),
            "prompt": prompt,
            "stream": False,
            "keep_alive": "60m",
            "options": {"temperature": 0.2},
        }
        # 30s budget: covers a cold model load into VRAM; warm calls take ~1s.
        resp = requests.post(_OLLAMA_GENERATE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        rewritten = (data.get("response") or "").strip()
    except Exception as exc:
        logger.warning("Ollama cleanup failed, falling back to stage-1: %s", exc)
        return stage1_result

    if not _sanity_check(text, rewritten):
        logger.warning("Ollama output failed sanity check, falling back to stage-1")
        return stage1_result

    return rewritten


def warmup(cfg) -> None:
    """Pre-load the Ollama model into VRAM so the first dictation's rewrite
    is fast. Fire-and-forget; call from a background thread at startup.
    """
    if not getattr(cfg, "clean_llm", True) or not ollama_available():
        return
    try:
        import requests

        requests.post(
            _OLLAMA_GENERATE_URL,
            json={
                "model": getattr(cfg, "ollama_model", "qwen2.5:7b-instruct"),
                "prompt": "ok",
                "stream": False,
                "keep_alive": "60m",
                "options": {"num_predict": 1},
            },
            timeout=120,
        )
        logger.info("Ollama model warmed up")
    except Exception as exc:
        logger.debug("Ollama warmup failed: %s", exc)


def unload(cfg) -> None:
    """Best-effort: evict the Ollama model from VRAM by asking it to stop
    keeping itself alive. Fire-and-forget; swallow all errors — this is a
    courtesy call made when switching to cloud backend, never load-bearing.
    """
    try:
        import requests

        requests.post(
            _OLLAMA_GENERATE_URL,
            json={
                "model": getattr(cfg, "ollama_model", "qwen2.5:7b-instruct"),
                "prompt": "",
                "stream": False,
                "keep_alive": 0,
            },
            timeout=5,
        )
        logger.info("Ollama model unload requested")
    except Exception as exc:
        logger.debug("Ollama unload failed (non-fatal): %s", exc)


def clean(text: str, cfg, app_context=None, previous=None) -> str:
    """Run the full cleanup pipeline: stage-1 rules, then optional stage-2
    Ollama rewrite.
    """
    if not text or not text.strip():
        return ""

    stage1_result = _stage1_rules(text, cfg)

    if not getattr(cfg, "clean_llm", True):
        return stage1_result

    if not stage1_result:
        return stage1_result

    if not ollama_available():
        return stage1_result

    return _stage2_llm_rewrite(text, cfg, stage1_result, app_context=app_context, previous=previous)
