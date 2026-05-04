from __future__ import annotations

import re
import urllib.parse
from html.parser import HTMLParser
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from apache_incubator_cwiki_mcp import cache, client

mcp = FastMCP("apache-incubator-cwiki-mcp")


@mcp.tool()
def cwiki_space_info(force_refresh: bool = False) -> dict[str, Any]:
    """Get metadata for the configured Apache Confluence space."""
    return client.confluence_get(
        f"/rest/api/space/{urllib.parse.quote(client.SPACE_KEY)}",
        force_refresh=force_refresh,
    )


@mcp.tool()
def cwiki_list_pages(limit: int = 25, start: int = 0, force_refresh: bool = False) -> dict[str, Any]:
    """List pages in the configured Apache Incubator Confluence space."""
    validate_range("limit", limit, 1, 100)
    validate_range("start", start, 0, 1_000_000)

    pages = client.confluence_get(
        "/rest/api/content",
        {
            "spaceKey": client.SPACE_KEY,
            "type": "page",
            "status": "current",
            "limit": str(limit),
            "start": str(start),
            "expand": "version",
        },
        force_refresh=force_refresh,
    )

    return {
        **client.pagination(pages),
        "pages": [client.page_summary(page) for page in pages.get("results", [])],
    }


@mcp.tool()
def cwiki_search_pages(
    query: str,
    limit: int = 10,
    start: int = 0,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Search pages in the configured Apache Incubator Confluence space."""
    if not query.strip():
        raise ValueError("query must not be empty")
    validate_range("limit", limit, 1, 50)
    validate_range("start", start, 0, 1_000_000)

    cql = f'space = "{escape_cql(client.SPACE_KEY)}" and type = page and text ~ "{escape_cql(query)}"'
    pages = client.confluence_get(
        "/rest/api/content/search",
        {
            "cql": cql,
            "limit": str(limit),
            "start": str(start),
            "expand": "body.view,version",
        },
        force_refresh=force_refresh,
    )

    return {
        "cql": cql,
        **client.pagination(pages),
        "pages": [
            {
                **client.page_summary(page),
                "excerpt": html_to_text(page.get("body", {}).get("view", {}).get("value", ""))[:800],
            }
            for page in pages.get("results", [])
        ],
    }


@mcp.tool()
def cwiki_get_page(
    title: str | None = None,
    page_id: str | None = None,
    format: Literal["plain", "view", "storage"] = "plain",
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Fetch a wiki page by page title or page id."""
    if not title and not page_id:
        raise ValueError("Provide either title or page_id.")

    page = (
        client.get_page_by_id(page_id, force_refresh=force_refresh)
        if page_id
        else client.get_page_by_title(title or "", force_refresh=force_refresh)
    )

    return {
        **client.page_summary(page),
        "ancestors": [
            {"id": ancestor.get("id"), "title": ancestor.get("title")}
            for ancestor in page.get("ancestors", [])
        ],
        "contentFormat": format,
        "content": page_content(page, format),
    }


@mcp.tool()
def cwiki_get_children(
    page_id: str,
    limit: int = 25,
    start: int = 0,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """List child pages for a page by page id."""
    if not page_id:
        raise ValueError("page_id must not be empty")
    validate_range("limit", limit, 1, 100)
    validate_range("start", start, 0, 1_000_000)

    children = client.confluence_get(
        f"/rest/api/content/{urllib.parse.quote(page_id)}/child/page",
        {
            "limit": str(limit),
            "start": str(start),
            "expand": "version",
        },
        force_refresh=force_refresh,
    )

    return {
        "parentId": page_id,
        **client.pagination(children),
        "pages": [client.page_summary(page) for page in children.get("results", [])],
    }


@mcp.tool()
def cwiki_cache_info() -> dict[str, Any]:
    """Show local Confluence response cache settings and current size."""
    entries = list(cache.cache_entries())
    size_bytes = sum(e.stat().st_size for e in entries if e.exists())
    return {
        "enabled": cache.cache_enabled(),
        "directory": str(cache.CACHE_DIR),
        "ttlSeconds": cache.CACHE_TTL_SECONDS,
        "entries": len(entries),
        "sizeBytes": size_bytes,
    }


@mcp.tool()
def cwiki_clear_cache() -> dict[str, Any]:
    """Clear the local Confluence response cache."""
    removed = cache.clear_cache()
    return {
        "directory": str(cache.CACHE_DIR),
        "removedEntries": removed,
    }


def page_content(page: dict[str, Any], format: Literal["plain", "view", "storage"]) -> str:
    body = page.get("body", {})
    if format == "storage":
        return body.get("storage", {}).get("value", "")

    view = body.get("view", {}).get("value") or body.get("storage", {}).get("value", "")
    return html_to_text(view) if format == "plain" else view


def validate_range(name: str, value: int, minimum: int, maximum: int) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def escape_cql(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = parser.text()
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


class _TextExtractor(HTMLParser):
    block_tags = {
        "blockquote",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "table",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.block_tags:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.block_tags:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._parts.append(data)

    def text(self) -> str:
        return re.sub(r"[ \t]+", " ", "".join(self._parts))
