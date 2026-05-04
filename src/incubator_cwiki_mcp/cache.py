from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

CACHE_TTL_SECONDS = int(os.getenv("CWIKI_CACHE_TTL_SECONDS", "2592000"))
CACHE_DIR = Path(os.getenv("CWIKI_CACHE_DIR", ".cache/cwiki")).expanduser()


def cache_enabled() -> bool:
    return CACHE_TTL_SECONDS > 0


def cache_entries() -> list[Path]:
    if not CACHE_DIR.exists():
        return []
    return sorted(CACHE_DIR.glob("*.json"))


def cache_file(path: str, base_url: str, space_key: str) -> Path:
    key = "\n".join([base_url, space_key, path])
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def read_cache(path: str, base_url: str, space_key: str) -> dict[str, Any] | None:
    if not cache_enabled():
        return None

    cache_path = cache_file(path, base_url, space_key)
    try:
        with cache_path.open("r", encoding="utf-8") as handle:
            entry = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    fetched_at = float(entry.get("fetchedAt", 0))
    if time.time() - fetched_at > CACHE_TTL_SECONDS:
        return None

    value = entry.get("value")
    return value if isinstance(value, dict) else None


def write_cache(path: str, value: dict[str, Any], base_url: str, space_key: str) -> None:
    if not cache_enabled():
        return

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = cache_file(path, base_url, space_key)
    tmp_path = cache_path.with_suffix(".tmp")
    entry = {
        "fetchedAt": time.time(),
        "path": path,
        "value": value,
    }
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(entry, handle, ensure_ascii=False)
    tmp_path.replace(cache_path)


def clear_cache() -> int:
    removed = 0
    for entry in cache_entries():
        try:
            entry.unlink()
            removed += 1
        except FileNotFoundError:
            pass
    return removed
