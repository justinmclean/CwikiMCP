from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from apache_incubator_cwiki_mcp import cache

BASE_URL = os.getenv("CWIKI_BASE_URL", "https://cwiki.apache.org/confluence").rstrip("/")
SPACE_KEY = os.getenv("CWIKI_SPACE_KEY", "INCUBATOR")


def confluence_get(
    path: str,
    query: dict[str, str] | None = None,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    suffix = ""
    if query:
        suffix = "?" + urllib.parse.urlencode(query)
    request_path = f"{path}{suffix}"

    if not force_refresh:
        cached = cache.read_cache(request_path, BASE_URL, SPACE_KEY)
        if cached is not None:
            return cached

    response = confluence_request(request_path)
    cache.write_cache(request_path, response, BASE_URL, SPACE_KEY)
    return response


def confluence_request(path: str) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "apache-incubator-cwiki-mcp/0.1.0",
    }

    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers=headers,
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Confluence returned non-JSON response: {body[:200]}") from error
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Confluence request failed: {error.code} {error.reason} {message[:500]}",
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Confluence request failed: {error.reason}") from error


def get_page_by_id(page_id: str | None, *, force_refresh: bool = False) -> dict[str, Any]:
    if not page_id:
        raise ValueError("page_id must not be empty")
    return confluence_get(
        f"/rest/api/content/{urllib.parse.quote(page_id)}",
        {"expand": "body.storage,body.view,version,history,ancestors,space"},
        force_refresh=force_refresh,
    )


def get_page_by_title(title: str, *, force_refresh: bool = False) -> dict[str, Any]:
    if not title.strip():
        raise ValueError("title must not be empty")

    pages = confluence_get(
        "/rest/api/content",
        {
            "spaceKey": SPACE_KEY,
            "title": title,
            "type": "page",
            "status": "current",
            "limit": "2",
            "expand": "body.storage,body.view,version,history,ancestors,space",
        },
        force_refresh=force_refresh,
    )
    results = pages.get("results", [])
    if not results:
        raise ValueError(f'No page found with title "{title}" in space {SPACE_KEY}.')
    if len(results) > 1:
        raise ValueError(f'More than one page matched title "{title}". Use page_id instead.')
    return results[0]


def page_summary(page: dict[str, Any]) -> dict[str, Any]:
    version = page.get("version", {})
    history = page.get("history", {})
    last_updated = history.get("lastUpdated", {})
    links = page.get("_links", {})
    return {
        "id": page.get("id"),
        "title": page.get("title"),
        "status": page.get("status"),
        "version": version.get("number"),
        "updated": version.get("when") or last_updated.get("when"),
        "updatedBy": version.get("by", {}).get("displayName") or last_updated.get("by", {}).get("displayName"),
        "url": f"{BASE_URL}{links['webui']}" if links.get("webui") else None,
    }


def pagination(result: dict[str, Any]) -> dict[str, Any]:
    links = result.get("_links", {})
    return {
        "start": result.get("start", 0),
        "limit": result.get("limit"),
        "size": result.get("size", len(result.get("results", []))),
        "hasNext": bool(links.get("next")),
        "next": f"{BASE_URL}{links['next']}" if links.get("next") else None,
    }
