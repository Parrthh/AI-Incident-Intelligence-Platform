import pytest

from iip.llm.redact import redact


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
        ("aws AKIAIOSFODNN7EXAMPLE leaked", "AKIAIOSFODNN7EXAMPLE", "aws_key"),
        ("key AIzaSyA1234567890abcdefghijklmnopqrstuv", "AIzaSyA1234567890", "google_key"),
        ("gh ghp_" + "a" * 36, "ghp_" + "a" * 36, "github_token"),
        ("slack xoxb-1234567890-abcdefghij", "xoxb-1234567890", "slack_token"),
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
