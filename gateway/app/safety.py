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
    "stripe_key": (re.compile(r"\b[rs]k_live_[A-Za-z0-9]{20,}\b"), True),
    "sendgrid_key": (re.compile(r"\bSG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{30,}\b"), True),
    "npm_token": (re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), True),
    "huggingface_token": (re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"), True),
    "render_key": (re.compile(r"\brnd_[A-Za-z0-9]{20,}\b"), True),
    "twilio_key": (re.compile(r"\bSK[0-9a-f]{32}\b"), True),
    "sentry_dsn": (re.compile(r"\bhttps?://[0-9a-f]{32}@[A-Za-z0-9.-]{1,100}/\d{1,12}\b"), False),
    "jwt": (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), False),
    "connection_string": (re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@]{1,80}:([^\s/@]{3,})@[^\s]{1,200}"), True),
}

_PRIVATE_KEY_HEAD = re.compile(r"-----BEGIN [A-Z ]{0,20}PRIVATE KEY-----")
_PRIVATE_KEY_END = "-----END "
_PRIVATE_KEY_MAX = 4000  # a key longer than this is cut off at the limit rather than searched for forever

# Context detectors: the value is judged by where it sits (a variable or header whose name says "secret") and by how
# random it looks, not by a vendor format. This is the layer scanners add after the format patterns.
_NAME = r"[A-Za-z0-9_.-]{0,40}(?:secret|token|passwd|password|pwd|api[_-]?key|apikey|credential|private[_-]?key|access[_-]?key|auth[_-]?key|dsn)[A-Za-z0-9_.-]{0,40}"
_ASSIGN = re.compile(rf"(?i)\b({_NAME})([\"']?\s*[:=]\s*[\"']?)([^\s\"'`,;<>(){{}}]{{8,200}})")
_BEARER = re.compile(r"(?i)\b(Bearer\s+)([A-Za-z0-9._~+/=-]{20,})")
_BASIC = re.compile(r"(?i)\b(Authorization\s*:\s*Basic\s+)([A-Za-z0-9+/=]{16,})")
_PASSWORD_NAME = re.compile(r"(?i)pass(?:wd|word)|pwd")

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

_PREFIX = re.compile(
    r"^(?:akia|asia|gw_live_|sk-(?:proj-|ant-|or-v1-)?|gsk_|gh[pousr]_|github_pat_|xox[abprs]-|aiza|[rs]k_live_|sg\.|npm_|hf_|rnd_|sk)"
)
# Text a person cannot see but a model reads: zero-width and direction-override characters, and the invisible
# "tag" block (ASCII smuggling). Tag characters are kept when they follow a flag base, where emoji flags use them.
_HIDDEN = re.compile("[\u200b\u2060\ufeff\u202a-\u202e\u2066-\u2069]")
_TAG_RUN = re.compile("[\U000e0000-\U000e007f]+")
_FLAG_BASE = "\U0001f3f4"
_CHAT_TOKENS = re.compile(r"<\|[A-Za-z_]{2,30}\|>|\[/?INST\]|<</?SYS>>", re.I)
NEUTRALIZED = "[neutralized: possible prompt injection]"

_PLACEHOLDER_WORDS = ("xxxx", "example", "your", "dummy", "sample", "placeholder", "changeme", "redacted", "0000", "1234", "abcd")
_PLACEHOLDER_PASSWORDS = {"password", "passwd", "pass", "secret", "changeme", "xxxx", "****", "postgres", "admin", "root", "test"}


# ---- helpers ----------------------------------------------------------------------------------------


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def _classes(s: str) -> int:
    return sum(bool(re.search(p, s)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]"))


def _generic_value_ok(name: str, value: str) -> bool:
    """Is `value`, assigned to something called `name`, plausibly a real secret rather than prose, a path or a template?"""
    if value[:1] in "<{$%*[" or value.lower().startswith(("http://", "https://", "file:", "/", "./", "../", "~")):
        return False
    low = value.lower()
    if low in {"true", "false", "null", "none", "undefined", "required", "optional", "string", "number", "boolean"}:
        return False
    if any(w in low for w in _PLACEHOLDER_WORDS):
        return False
    minimum = 8 if _PASSWORD_NAME.search(name) else 16  # people choose shorter passwords than machines choose keys
    return len(value) >= minimum and _classes(value) >= 2 and _entropy(value) >= (2.8 if minimum == 8 else 3.2)


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
    injection: int = 0  # override phrases and chat-template tokens
    hidden: int = 0  # invisible or direction-overriding characters

    def merge(self, other: Findings) -> None:
        self.secrets.update(other.secrets)
        self.pii.update(other.pii)
        self.injection += other.injection
        self.hidden += other.hidden

    @property
    def any(self) -> bool:
        return bool(self.secrets or self.pii or self.injection or self.hidden)


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


def _context_secrets(text: str, counter: Counter[str] | None) -> str:
    """Redact values by context. Counts are by kind; names are kept so the surrounding text still makes sense."""

    def assign(m: re.Match[str]) -> str:
        name, value = m.group(1), m.group(3)
        if "[REDACTED:" in value or not _generic_value_ok(name, value):
            return m.group(0)
        if counter is not None:
            counter["generic_secret"] += 1
        return f"{name}{m.group(2)}[REDACTED:generic_secret]"

    def bearer(kind: str) -> Callable[[re.Match[str]], str]:
        def repl(m: re.Match[str]) -> str:
            if "[REDACTED:" in m.group(2) or _entropy(m.group(2)) < 3.0 or any(w in m.group(2).lower() for w in _PLACEHOLDER_WORDS):
                return m.group(0)
            if counter is not None:
                counter[kind] += 1
            return f"{m.group(1)}[REDACTED:{kind}]"

        return repl

    text = _ASSIGN.sub(assign, text)
    text = _BEARER.sub(bearer("bearer_token"), text)
    return _BASIC.sub(bearer("basic_auth"), text)


def _secret_sub(kind: str, check_placeholder: bool, counter: Counter[str] | None) -> Callable[[re.Match[str]], str]:
    def repl(m: re.Match[str]) -> str:
        if check_placeholder and _is_placeholder(kind, m):
            return m.group(0)
        if counter is not None:
            counter[kind] += 1
        return f"[REDACTED:{kind}]"

    return repl


# ---- public API -------------------------------------------------------------------------------------


def _hidden_count(text: str) -> int:
    n = len(_HIDDEN.findall(text))
    for m in _TAG_RUN.finditer(text):
        if text[m.start() - 1 : m.start()] != _FLAG_BASE:
            n += len(m.group(0))
    return n


def defang_input(text: str) -> str:
    """Make injection attempts inert text: strip hidden characters and replace override phrases and chat-template
    tokens with a visible marker. The model can still see that something was there and answer questions about it."""
    text = _TAG_RUN.sub(lambda m: m.group(0) if text[m.start() - 1 : m.start()] == _FLAG_BASE else "", text)
    text = _HIDDEN.sub("", text)
    text = _INJECTION.sub(NEUTRALIZED, text)
    return _CHAT_TOKENS.sub(NEUTRALIZED, text)


def scan(text: str) -> Findings:
    """What `text` contains. Nothing is modified."""
    f = Findings()
    if not text:
        return f
    _private_keys(text, f.secrets)
    for kind, (pat, check) in SECRET_PATTERNS.items():
        pat.sub(_secret_sub(kind, check, f.secrets), text)
    _context_secrets(text, f.secrets)
    f.pii["email"] = len(_EMAIL.findall(text))
    f.pii["card"] = sum(1 for m in _CARD.finditer(text) if _card_ok(m.group(0)))
    f.pii["ssn"] = len(_SSN.findall(text))
    f.pii = Counter({k: v for k, v in f.pii.items() if v})
    f.injection = len(_INJECTION.findall(text)) + len(_CHAT_TOKENS.findall(text))
    f.hidden = _hidden_count(text)
    return f


def redact(text: str, *, secrets: bool = False, pii: bool = False) -> str:
    """`text` with the chosen kinds replaced by `[REDACTED:kind]`."""
    if secrets:
        text = _private_keys(text, None)
        for kind, (pat, check) in SECRET_PATTERNS.items():
            text = pat.sub(_secret_sub(kind, check, None), text)
        text = _context_secrets(text, None)
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


# Injection is judged on what other people write (users, tool results, earlier assistant turns). The system prompt is the
# operator's own text and may legitimately talk about instructions, so it is only checked for secrets and personal data.
INJECTION_ROLES = ("user", "tool", "function", "assistant")


def scan_messages(messages: list[ChatMessage]) -> Findings:
    f = Findings()
    for m in messages:
        for t in _texts(m.content):
            found = scan(t)
            if m.role not in INJECTION_ROLES:
                found.injection = found.hidden = 0
            f.merge(found)
    return f


def redact_messages(
    messages: list[ChatMessage], *, secrets: bool, pii: bool, defang: bool = False
) -> list[ChatMessage]:
    def clean(text: str, role: str) -> str:
        text = redact(text, secrets=secrets, pii=pii)
        return defang_input(text) if defang and role in INJECTION_ROLES else text

    out: list[ChatMessage] = []
    for m in messages:
        c = m.content
        if isinstance(c, str):
            c = clean(c, m.role)
        elif isinstance(c, list):
            c = [
                {**p, "text": clean(p["text"], m.role)} if isinstance(p, dict) and isinstance(p.get("text"), str) else p
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


# ---- output: make the reply inert for whatever displays or processes it ----------------------------------------

_FENCE_OR_CODE = re.compile(r"```[\s\S]*?(?:```|$)|`[^`\n]{1,300}`")
_TAG = re.compile(r"<\/?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]{0,2000})?\/?>")
_TAG_NAME = re.compile(r"<\/?([A-Za-z][A-Za-z0-9-]*)")
_DANGEROUS_ELEMENTS = frozenset({
    "script", "iframe", "object", "embed", "applet", "style", "link", "meta", "base", "form", "svg", "math",
    "template", "noscript", "frame", "frameset", "img", "video", "audio", "source", "input", "button", "textarea",
})
_ACTIVE = re.compile(r"\bon[a-z]+\s*=|(?:javascript|vbscript)\s*:|data\s*:\s*text/html|srcdoc\s*=", re.I)
_MD_IMAGE = re.compile(r"!\[([^\]\n]{0,200})\]\(\s*(https?://[^)\s]{1,500})[^)]{0,200}\)")
_MD_BAD_LINK = re.compile(r"\]\(\s*(javascript|vbscript|data)\s*:", re.I)
_URL = re.compile(r"^(https?)://([^/\s?#]+)(.*)$", re.I)


def defang_url(url: str) -> str:
    """hxxps://example[.]com/path : readable, but not a link and not fetched by anything that follows links."""
    m = _URL.match(url)
    if not m:
        return url
    return f"hxx{m.group(1)[3:]}://{m.group(2).replace('.', '[.]')}{m.group(3)}"


def _defang_plain(text: str, counts: Counter[str]) -> str:
    def tag(m: re.Match[str]) -> str:
        name = _TAG_NAME.match(m.group(0))
        if (name and name.group(1).lower() in _DANGEROUS_ELEMENTS) or _ACTIVE.search(m.group(0)):
            counts["html"] += 1
            return m.group(0).replace("<", "&lt;").replace(">", "&gt;")
        return m.group(0)

    def image(m: re.Match[str]) -> str:
        counts["image"] += 1
        alt = re.sub(r"[\[\]()]", "", m.group(1)).strip()
        return f"[image removed: {alt or 'no description'}] ({defang_url(m.group(2))})"

    def link(m: re.Match[str]) -> str:
        counts["link"] += 1
        return f"](#blocked-{m.group(1).lower()}:"

    text = _MD_IMAGE.sub(image, text)
    text = _MD_BAD_LINK.sub(link, text)
    return _TAG.sub(tag, text)


def defang_markup(text: str) -> tuple[str, Counter[str]]:
    """Neutralize what could make a browser, a renderer or a console act on a model reply: script-like and
    auto-fetching HTML elements, event-handler and javascript: attributes, javascript: links, and markdown images
    (a classic way to leak data by auto-fetching a URL). Code blocks and inline code are left alone: they are
    shown as code, and rewriting them would break the answer. Whoever extracts and runs code from a reply must
    still treat it as untrusted."""
    counts: Counter[str] = Counter()
    out: list[str] = []
    pos = 0
    for m in _FENCE_OR_CODE.finditer(text):
        out.append(_defang_plain(text[pos : m.start()], counts))
        out.append(m.group(0))
        pos = m.end()
    out.append(_defang_plain(text[pos:], counts))
    return "".join(out), counts


def declared_tool_names(request_extra: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for t in request_extra.get("tools") or []:
        fn = t.get("function") if isinstance(t, dict) else None
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            names.add(fn["name"])
    for f in request_extra.get("functions") or []:
        if isinstance(f, dict) and isinstance(f.get("name"), str):
            names.add(f["name"])
    return names


def called_tool_names(message: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for c in message.get("tool_calls") or []:
        fn = c.get("function") if isinstance(c, dict) else None
        names.append(str(fn.get("name")) if isinstance(fn, dict) else "")
    fc = message.get("function_call")
    if isinstance(fc, dict):
        names.append(str(fc.get("name")))
    return names
