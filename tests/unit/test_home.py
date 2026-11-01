from fastapi.testclient import TestClient


def test_home_page_renders_with_build_info(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "AI Incident Intelligence Platform" in response.text
    assert "test-sha" in response.text
    assert 'href="/readyz"' in response.text


def test_home_answers_head_requests(client: TestClient) -> None:
    # Hosting platforms and uptime monitors often probe with HEAD.
    assert client.head("/").status_code == 200
