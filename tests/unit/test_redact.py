import pytest

from iip.llm.redact import redact

# Fake credentials are assembled at runtime from harmless pieces, so no
# key-shaped literal sits in the source for secret scanners to flag. None of
# these are real keys.
FAKE_GOOGLE_KEY = "AI" + "za" + "Sy" + "X" * 33
FAKE_AWS_KEY = "AK" + "IA" + "TESTONLY" + "0" * 8
FAKE_GITHUB_TOKEN = "gh" + "p_" + "t" * 36
FAKE_SLACK_TOKEN = "xo" + "xb-" + "0" * 10 + "-testonly"


@pytest.mark.parametrize(
    ("text", "secret", "kind"),
    [
        ("db at postgresql://app:hunter2@db.internal:5432/x", "hunter2", "url_password"),
        ("Authorization: Bearer abcdef1234567890xyz", "abcdef1234567890xyz", "bearer"),
        (
            "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJlc2ln here",
            "eyJhbGciOiJIUzI1NiJ9",
            "jwt",
        ),
        (f"aws {FAKE_AWS_KEY} leaked", FAKE_AWS_KEY, "aws_key"),
        (f"key {FAKE_GOOGLE_KEY}", FAKE_GOOGLE_KEY, "google_key"),
        (f"gh {FAKE_GITHUB_TOKEN}", FAKE_GITHUB_TOKEN, "github_token"),
        (f"slack {FAKE_SLACK_TOKEN}", FAKE_SLACK_TOKEN, "slack_token"),
        ("api_key=supersecretvalue123 rest", "supersecretvalue123", "secret_kv"),
        ('DB_PASSWORD: "p@ss w0rd"', "p@ss w0rd", "secret_kv"),
        ("contact oncall.person@example.com now", "oncall.person@example.com", "email"),
        (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
            "MIIEow",
            "private_key",
        ),
    ],
)
def test_redacts_secrets(text: str, secret: str, kind: str) -> None:
    result = redact(text)

    assert secret not in result.text
    assert result.counts.get(kind, 0) >= 1
    assert "[REDACTED:" in result.text


def test_url_password_keeps_user_and_host() -> None:
    result = redact("postgresql://app:hunter2@db.internal:5432/x")

    assert result.text == "postgresql://app:[REDACTED:password]@db.internal:5432/x"


def test_leaves_ordinary_alert_text_alone() -> None:
    text = "checkout error rate above 5% for 2 minutes; service=checkout severity=critical"

    result = redact(text)

    assert result.text == text
    assert result.total == 0
