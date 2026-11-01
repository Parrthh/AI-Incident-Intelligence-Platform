from fastapi.testclient import TestClient


def test_home_renders_example_without_a_database(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "AI Incident Intelligence Platform" in html
    # No database in unit tests: the captured chaos-run example is shown, labeled as such.
    assert "Example from a chaos test run" in html
    assert "100% of payment spans" in html
    assert 'href="/incidents"' in html
    assert "test-sha" in html  # build info in the footer


def test_home_answers_head_requests(client: TestClient) -> None:
    # Hosting platforms and uptime monitors often probe with HEAD.
    assert client.head("/").status_code == 200


def test_summary_credit_is_plain_language(client: TestClient) -> None:
    html = client.get("/").text

    assert "Written by gemini-3.5-flash-lite, prompt version 0" in html
    assert "summary_v0" not in html
    assert "In active development" not in html
