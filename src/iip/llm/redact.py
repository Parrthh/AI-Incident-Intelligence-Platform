"""Remove secrets and personal data from text before it goes to an LLM provider.

Alert labels and annotations are written by humans and tools, and sometimes carry
things that must not leave the system: tokens in URLs, passwords in connection
strings, email addresses. Anything sent to a hosted LLM leaves our control, so
every prompt input passes through ``redact`` first.

This is a first version covering the common cases. M6 extends it (log lines,
configurable patterns, high-entropy detection). Each pattern replaces the
sensitive part with a typed placeholder like ``[REDACTED:email]``, so the model
still knows something was there.
"""

import re
from dataclasses import dataclass

_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # Private keys (PEM blocks).
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED:private_key]",
    ),
    # user:password@ in URLs and connection strings; keep the user and host.
    ("url_password", re.compile(r"(?<=://)([^:/@\s]+):([^@\s]+)@"), r"\1:[REDACTED:password]@"),
    # Authorization headers and bearer tokens.
    (
        "bearer",
        re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
        r"\1 [REDACTED:token]",
    ),
    # JSON Web Tokens: three base64url segments starting with eyJ.
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
        "[REDACTED:jwt]",
    ),
    # Well-known API key shapes.
    ("aws_key", re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"), "[REDACTED:aws_key]"),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "[REDACTED:api_key]"),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "[REDACTED:api_key]"),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED:api_key]"),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), "[REDACTED:api_key]"),
    # key=value / key: value where the key name says it's a secret.
    (
        "secret_kv",
        re.compile(
            r"(?i)\b([a-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)"
            r"[a-z0-9_.-]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
        ),
        r"\1\2[REDACTED:secret]",
    ),
    (
        "email",
        # Not right after ":" or "/", which is the user:password@host part of a URL
        # (handled above), not an email address.
        re.compile(r"(?<![:/])\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED:email]",
    ),
]


@dataclass(frozen=True)
class RedactionResult:
    text: str
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def redact(text: str) -> RedactionResult:
    counts: dict[str, int] = {}
    for name, pattern, replacement in _PATTERNS:
        text, n = pattern.subn(replacement, text)
        if n:
            counts[name] = counts.get(name, 0) + n
    return RedactionResult(text=text, counts=counts)
