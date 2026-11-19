"""Content checks for the safety gate: secrets, personal data, prompt-injection phrases.

Pure functions, no I/O. The gate (graph/nodes.py) decides what to do with what is found; the matched text
itself is never logged or returned, only the kinds and counts.

These are patterns, not a classifier. They are tuned to avoid false alarms on documentation and placeholders
(`sk-xxxxxxxx`, `postgres://user:password@localhost`), so they will miss obfuscated or unusual secrets. Treat
them as a seatbelt, not as a guarantee.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .schemas import ChatMessage

# ---- patterns ---------------------------------------------------------------------------------------

# kind -> (compiled pattern, whether to apply the placeholder filter to the match)
SECRET_PATTERNS: dict[str, tuple[re.Pattern[str], bool]] = {
    "aws_access_key": (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), True),
    "gateway_key": (re.compile(r"\bgw_live_[A-Za-z0-9_-]{20,}\b"), True),
    "api_key": (re.compile(r"\bsk-(?:proj-|ant-|or-v1-)?[A-Za-z0-9_-]{20,}\b"), True),
    "groq_key": (re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"), True),
    "github_token": (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"), True),
    "slack_token": (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), True),
    "google_api_key": (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), True),
    "jwt": (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), False),
    "connection_string": (re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@]{1,80}:([^\s/@]{3,})@[^\s]{1,200}"), True),
}

_PRIVATE_KEY_HEAD = re.compile(r"-----BEGIN [A-Z ]{0,20}PRIVATE KEY-----")
_PRIVATE_KEY_END = "-----END "
_PRIVATE_KEY_MAX = 4000  # a key longer than this is cut off at the limit rather than searched for forever

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")

_INJECTION = re.compile(
    r"ignore\s+(?:all\s+|any\s+|the\s+)*(?:previous|prior|above|earlier)\s+(?:instructions|prompts?|rules)"
    r"|disregard\s+(?:all\s+|any\s+|the\s+)*(?:previous|prior|above|earlier|system)\s+(?:instructions|prompts?|rules)"
    r"|(?:reveal|print|show|repeat|output)\s+(?:your|the)\s+(?:system|hidden|initial)\s+(?:prompt|instructions)"
    r"|you\s+are\s+now\s+(?:dan\b|in\s+developer\s+mode)",
    re.I,
)

_PREFIX = re.compile(r"^(?:akia|asia|gw_live_|sk-(?:proj-|ant-|or-v1-)?|gsk_|gh[pousr]_|github_pat_|xox[abprs]-|aiza)")
_PLACEHOLDER_WORDS = ("xxxx", "example", "your", "dummy", "sample", "placeholder", "changeme", "redacted", "0000", "1234", "abcd")
_PLACEHOLDER_PASSWORDS = {"password", "passwd", "pass", "secret", "changeme", "xxxx", "****", "postgres", "admin", "root", "test"}


# ---- helpers ----------------------------------------------------------------------------------------


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def _is_placeholder(kind: str, match: re.Match[str]) -> bool:
    """Documentation and examples are not secrets: a real key looks random, a placeholder does not."""
    if kind == "connection_string":
        pw = match.group(1)
        return pw.lower() in _PLACEHOLDER_PASSWORDS or pw[:1] in "<{$*" or _entropy(pw) < 2.0
    text = match.group(0)
    low = text.lower()
    if any(w in low for w in _PLACEHOLDER_WORDS):
        return True
    body = _PREFIX.sub("", low)
    return _entropy(body) < 3.0


def luhn(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if flip:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        flip = not flip
    return total % 10 == 0


def _card_ok(raw: str) -> bool:
    digits = re.sub(r"[ -]", "", raw)
    return 13 <= len(digits) <= 19 and len(set(digits)) > 1 and luhn(digits)


@dataclass
class Findings:
    secrets: Counter[str] = field(default_factory=Counter)
    pii: Counter[str] = field(default_factory=Counter)
    injection: int = 0

    def merge(self, other: Findings) -> None:
        self.secrets.update(other.secrets)
        self.pii.update(other.pii)
        self.injection += other.injection

    @property
    def any(self) -> bool:
        return bool(self.secrets or self.pii or self.injection)


def _private_keys(text: str, counter: Counter[str] | None) -> str:
    """Replace private-key blocks. Linear: each marker looks ahead a bounded distance with str.find, so a
    request full of markers cannot make this quadratic."""
    out: list[str] = []
    pos = 0
    while True:
        m = _PRIVATE_KEY_HEAD.search(text, pos)
        if m is None:
            break
        window_end = min(len(text), m.end() + _PRIVATE_KEY_MAX)
        end = text.find(_PRIVATE_KEY_END, m.end(), window_end)
        stop = window_end
        if end != -1:
            close = text.find("-----", end + len(_PRIVATE_KEY_END), min(len(text), end + 80))
            stop = close + 5 if close != -1 else end + len(_PRIVATE_KEY_END)
        out.append(text[pos:m.start()])
        out.append("[REDACTED:private_key]")
        if counter is not None:
            counter["private_key"] += 1
        pos = stop
    out.append(text[pos:])
    return "".join(out)


def _secret_sub(kind: str, check_placeholder: bool, counter: Counter[str] | None) -> Callable[[re.Match[str]], str]:
    def repl(m: re.Match[str]) -> str:
        if check_placeholder and _is_placeholder(kind, m):
            return m.group(0)
        if counter is not None:
            counter[kind] += 1
        return f"[REDACTED:{kind}]"

    return repl


# ---- public API -------------------------------------------------------------------------------------


def scan(text: str) -> Findings:
    """What `text` contains. Nothing is modified."""
    f = Findings()
    if not text:
        return f
    _private_keys(text, f.secrets)
    for kind, (pat, check) in SECRET_PATTERNS.items():
        pat.sub(_secret_sub(kind, check, f.secrets), text)
    f.pii["email"] = len(_EMAIL.findall(text))
    f.pii["card"] = sum(1 for m in _CARD.finditer(text) if _card_ok(m.group(0)))
    f.pii["ssn"] = len(_SSN.findall(text))
    f.pii = Counter({k: v for k, v in f.pii.items() if v})
    f.injection = len(_INJECTION.findall(text))
    return f


def redact(text: str, *, secrets: bool = False, pii: bool = False) -> str:
    """`text` with the chosen kinds replaced by `[REDACTED:kind]`."""
    if secrets:
        text = _private_keys(text, None)
        for kind, (pat, check) in SECRET_PATTERNS.items():
            text = pat.sub(_secret_sub(kind, check, None), text)
    if pii:
        text = _EMAIL.sub("[REDACTED:email]", text)
        text = _CARD.sub(lambda m: "[REDACTED:card]" if _card_ok(m.group(0)) else m.group(0), text)
        text = _SSN.sub("[REDACTED:ssn]", text)
    return text


def _texts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str)]
    return []


def message_chars(messages: list[ChatMessage]) -> int:
    return sum(len(t) for m in messages for t in _texts(m.content))


def scan_messages(messages: list[ChatMessage]) -> Findings:
    f = Findings()
    for m in messages:
        for t in _texts(m.content):
            f.merge(scan(t))
    return f


def redact_messages(messages: list[ChatMessage], *, secrets: bool, pii: bool) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    for m in messages:
        c = m.content
        if isinstance(c, str):
            c = redact(c, secrets=secrets, pii=pii)
        elif isinstance(c, list):
            c = [
                {**p, "text": redact(p["text"], secrets=secrets, pii=pii)}
                if isinstance(p, dict) and isinstance(p.get("text"), str)
                else p
                for p in c
            ]
        out.append(m.model_copy(update={"content": c}))
    return out


def reply_text(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    content = (choices[0].get("message") or {}).get("content")
    return content if isinstance(content, str) else ""


def reply_is_empty(body: dict[str, Any]) -> bool:
    """No text and no tool call: nothing the caller can use."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False  # a malformed body is its own error class
    msg = choices[0].get("message") or {}
    if msg.get("tool_calls") or msg.get("function_call"):
        return False
    return not reply_text(body).strip()
