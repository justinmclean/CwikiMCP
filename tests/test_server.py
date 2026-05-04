from __future__ import annotations

import json
import time
import urllib.error

import pytest

from incubator_cwiki_mcp import cache, client, tools


@pytest.fixture(autouse=True)
def isolated_server(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(client, "BASE_URL", "https://example.test/confluence")
    monkeypatch.setattr(client, "SPACE_KEY", "INCUBATOR")
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "CACHE_TTL_SECONDS", 21_600)


def page(page_id: str = "123", title: str = "Home") -> dict:
    return {
        "id": page_id,
        "type": "page",
        "status": "current",
        "title": title,
        "version": {
            "number": 7,
            "when": "2026-02-22T00:00:00.000Z",
            "by": {"displayName": "ASF Infrabot"},
        },
        "history": {
            "lastUpdated": {
                "when": "2026-02-21T00:00:00.000Z",
                "by": {"displayName": "Someone Else"},
            },
        },
        "ancestors": [{"id": "1", "title": "Parent"}],
        "body": {
            "view": {"value": "<h1>Apache Incubator</h1><p>Hello &amp; welcome.</p>"},
            "storage": {"value": "<p>Storage body</p>"},
        },
        "_links": {"webui": "/display/INCUBATOR/Home"},
    }


def result(*pages: dict, start: int = 0, limit: int = 25, next_link: str | None = None) -> dict:
    links = {"next": next_link} if next_link else {}
    return {
        "results": list(pages),
        "start": start,
        "limit": limit,
        "size": len(pages),
        "_links": links,
    }


def patch_request(monkeypatch: pytest.MonkeyPatch, response_for_path):
    calls: list[str] = []

    def fake_request(path: str) -> dict:
        response = response_for_path(path)
        calls.append(path)
        return response

    monkeypatch.setattr(client, "confluence_request", fake_request)
    return calls


def test_space_info_uses_configured_space(monkeypatch: pytest.MonkeyPatch):
    calls = patch_request(monkeypatch, lambda path: {"key": "INCUBATOR", "path": path})

    response = tools.cwiki_space_info()

    assert response == {"key": "INCUBATOR", "path": "/rest/api/space/INCUBATOR"}
    assert calls == ["/rest/api/space/INCUBATOR"]


def test_list_pages_returns_summaries_and_pagination(monkeypatch: pytest.MonkeyPatch):
    calls = patch_request(
        monkeypatch,
        lambda path: result(page("123", "Home"), start=25, limit=1, next_link="/rest/api/content?start=26"),
    )

    response = tools.cwiki_list_pages(limit=1, start=25)

    assert response["start"] == 25
    assert response["limit"] == 1
    assert response["size"] == 1
    assert response["hasNext"] is True
    assert response["next"] == "https://example.test/confluence/rest/api/content?start=26"
    assert response["pages"] == [
        {
            "id": "123",
            "title": "Home",
            "status": "current",
            "version": 7,
            "updated": "2026-02-22T00:00:00.000Z",
            "updatedBy": "ASF Infrabot",
            "url": "https://example.test/confluence/display/INCUBATOR/Home",
        }
    ]
    assert calls[0].startswith("/rest/api/content?")
    assert "spaceKey=INCUBATOR" in calls[0]
    assert "limit=1" in calls[0]
    assert "start=25" in calls[0]


def test_list_pages_validates_ranges():
    with pytest.raises(ValueError, match="limit must be between 1 and 100"):
        tools.cwiki_list_pages(limit=0)

    with pytest.raises(ValueError, match="start must be between 0 and 1000000"):
        tools.cwiki_list_pages(start=-1)


def test_search_pages_returns_cql_and_text_excerpt(monkeypatch: pytest.MonkeyPatch):
    calls = patch_request(monkeypatch, lambda path: result(page("99", "Search Hit"), limit=10))

    response = tools.cwiki_search_pages('license "notice"', limit=10)

    assert response["cql"] == 'space = "INCUBATOR" and type = page and text ~ "license \\"notice\\""'
    assert response["pages"][0]["title"] == "Search Hit"
    assert response["pages"][0]["excerpt"] == "Apache Incubator\nHello & welcome."
    assert calls[0].startswith("/rest/api/content/search?")
    assert "body.view%2Cversion" in calls[0]


def test_search_pages_rejects_empty_query():
    with pytest.raises(ValueError, match="query must not be empty"):
        tools.cwiki_search_pages("   ")


def test_get_page_by_title_returns_plain_content(monkeypatch: pytest.MonkeyPatch):
    calls = patch_request(monkeypatch, lambda path: result(page("123", "Home"), limit=2))

    response = tools.cwiki_get_page(title="Home")

    assert response["id"] == "123"
    assert response["title"] == "Home"
    assert response["ancestors"] == [{"id": "1", "title": "Parent"}]
    assert response["contentFormat"] == "plain"
    assert response["content"] == "Apache Incubator\nHello & welcome."
    assert calls[0].startswith("/rest/api/content?")
    assert "title=Home" in calls[0]


def test_get_page_by_id_returns_storage_content(monkeypatch: pytest.MonkeyPatch):
    calls = patch_request(monkeypatch, lambda path: page("123", "Home"))

    response = tools.cwiki_get_page(page_id="123", format="storage")

    assert response["contentFormat"] == "storage"
    assert response["content"] == "<p>Storage body</p>"
    assert calls == [
        "/rest/api/content/123?expand=body.storage%2Cbody.view%2Cversion%2Chistory%2Cancestors%2Cspace"
    ]


def test_get_page_by_id_returns_view_content(monkeypatch: pytest.MonkeyPatch):
    patch_request(monkeypatch, lambda path: page("123", "Home"))

    response = tools.cwiki_get_page(page_id="123", format="view")

    assert response["contentFormat"] == "view"
    assert response["content"] == "<h1>Apache Incubator</h1><p>Hello &amp; welcome.</p>"


def test_get_page_requires_title_or_id():
    with pytest.raises(ValueError, match="Provide either title or page_id"):
        tools.cwiki_get_page()


def test_get_page_by_title_reports_missing_page(monkeypatch: pytest.MonkeyPatch):
    patch_request(monkeypatch, lambda path: result())

    with pytest.raises(ValueError, match='No page found with title "Missing"'):
        tools.cwiki_get_page(title="Missing")


def test_get_page_by_title_reports_duplicate_titles(monkeypatch: pytest.MonkeyPatch):
    patch_request(monkeypatch, lambda path: result(page("1", "Home"), page("2", "Home")))

    with pytest.raises(ValueError, match="More than one page matched"):
        tools.cwiki_get_page(title="Home")


def test_get_children_returns_child_summaries(monkeypatch: pytest.MonkeyPatch):
    calls = patch_request(monkeypatch, lambda path: result(page("456", "Child"), start=0, limit=5))

    response = tools.cwiki_get_children("123", limit=5)

    assert response["parentId"] == "123"
    assert response["pages"][0]["id"] == "456"
    assert response["pages"][0]["title"] == "Child"
    assert calls == ["/rest/api/content/123/child/page?limit=5&start=0&expand=version"]


def test_get_children_validates_inputs():
    with pytest.raises(ValueError, match="page_id must not be empty"):
        tools.cwiki_get_children("")

    with pytest.raises(ValueError, match="limit must be between 1 and 100"):
        tools.cwiki_get_children("123", limit=101)


def test_cache_info_and_clear_cache():
    cache.write_cache("/one", {"value": 1}, client.BASE_URL, client.SPACE_KEY)
    cache.write_cache("/two", {"value": 2}, client.BASE_URL, client.SPACE_KEY)

    info = tools.cwiki_cache_info()
    assert info["enabled"] is True
    assert info["entries"] == 2
    assert info["sizeBytes"] > 0

    cleared = tools.cwiki_clear_cache()
    assert cleared["removedEntries"] == 2
    assert tools.cwiki_cache_info()["entries"] == 0


def test_confluence_get_uses_cache(monkeypatch: pytest.MonkeyPatch):
    calls = patch_request(monkeypatch, lambda path: {"path": path, "count": len(calls) + 1})

    first = client.confluence_get("/rest/api/content", {"limit": "1"})
    second = client.confluence_get("/rest/api/content", {"limit": "1"})

    assert first == second
    assert len(calls) == 1


def test_confluence_get_force_refresh_bypasses_cache(monkeypatch: pytest.MonkeyPatch):
    calls = patch_request(monkeypatch, lambda path: {"path": path, "count": len(calls) + 1})

    first = client.confluence_get("/rest/api/content")
    second = client.confluence_get("/rest/api/content", force_refresh=True)

    assert first["count"] == 1
    assert second["count"] == 2
    assert len(calls) == 2


def test_cache_can_be_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cache, "CACHE_TTL_SECONDS", 0)
    calls = patch_request(monkeypatch, lambda path: {"count": len(calls) + 1})

    first = client.confluence_get("/rest/api/content")
    second = client.confluence_get("/rest/api/content")

    assert first["count"] == 1
    assert second["count"] == 2
    assert tools.cwiki_cache_info()["enabled"] is False


def test_expired_cache_entry_is_ignored(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cache, "CACHE_TTL_SECONDS", 1)
    cache_path = cache.cache_file("/rest/api/content", client.BASE_URL, client.SPACE_KEY)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps({"fetchedAt": time.time() - 10, "value": {"stale": True}}),
        encoding="utf-8",
    )
    patch_request(monkeypatch, lambda path: {"fresh": True})

    response = client.confluence_get("/rest/api/content")

    assert response == {"fresh": True}


def test_invalid_cache_entry_is_ignored(monkeypatch: pytest.MonkeyPatch):
    cache_path = cache.cache_file("/rest/api/content", client.BASE_URL, client.SPACE_KEY)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text("{", encoding="utf-8")
    patch_request(monkeypatch, lambda path: {"fresh": True})

    assert client.confluence_get("/rest/api/content") == {"fresh": True}


def test_get_page_by_id_rejects_empty_id():
    with pytest.raises(ValueError, match="page_id must not be empty"):
        client.get_page_by_id("")


def test_get_page_by_title_rejects_blank_title():
    with pytest.raises(ValueError, match="title must not be empty"):
        client.get_page_by_title("   ")


def test_html_to_text_collapses_markup_and_entities():
    assert tools.html_to_text("<h1>Title</h1><p>A&nbsp;&amp;&nbsp;B</p>") == "Title\nA\xa0&\xa0B"


def test_html_to_text_ignores_inline_tags():
    assert tools.html_to_text("<p>Hello <strong>world</strong></p>") == "Hello world"


def test_confluence_request_success(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["timeout"] = timeout
        captured["user_agent"] = request.headers["User-agent"]
        return Response()

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)

    assert client.confluence_request("/rest/api/space/INCUBATOR") == {"ok": True}
    assert captured == {
        "url": "https://example.test/confluence/rest/api/space/INCUBATOR",
        "method": "GET",
        "timeout": 30,
        "user_agent": "apache-incubator-cwiki-mcp/0.1.0",
    }


def test_confluence_request_reports_http_errors(monkeypatch: pytest.MonkeyPatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            404,
            "Not Found",
            hdrs=None,
            fp=ErrorBody(b"missing"),
        )

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Confluence request failed: 404 Not Found missing"):
        client.confluence_request("/rest/api/missing")


def test_confluence_request_reports_non_json_response(monkeypatch: pytest.MonkeyPatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"<html>not json</html>"

    monkeypatch.setattr(client.urllib.request, "urlopen", lambda req, timeout: Response())

    with pytest.raises(RuntimeError, match="Confluence returned non-JSON response"):
        client.confluence_request("/rest/api/content")


def test_confluence_request_reports_url_errors(monkeypatch: pytest.MonkeyPatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Confluence request failed: no route"):
        client.confluence_request("/rest/api/content")


class ErrorBody:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def close(self):
        pass
