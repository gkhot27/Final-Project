"""Polite, cached HTTP client for SEC endpoints (User-Agent + rate limit + retries)."""
import json
import sys
import time
from pathlib import Path

import requests

import config

_session = None
_last_call = 0.0


def _get_session():
    global _session
    if "CHANGE ME" in config.USER_AGENT:
        sys.exit("Set your contact first:  export SEC_USER_AGENT=\"Your Name you@email.com\"  "
                 "(or edit USER_AGENT in config.py). The SEC rejects anonymous requests.")
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": config.USER_AGENT,
                                 "Accept-Encoding": "gzip, deflate"})
    return _session


def get(url, params=None, stream=False, max_retries=5):
    """GET with throttling and exponential backoff on 429/5xx."""
    global _last_call
    s = _get_session()
    for attempt in range(max_retries):
        wait = (1.0 / config.MAX_REQUESTS_PER_SEC) - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        try:
            r = s.get(url, params=params, stream=stream, timeout=60)
        except requests.RequestException as e:
            print(f"  network error ({e}); retrying", file=sys.stderr)
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            print(f"  HTTP {r.status_code} on {url}; backing off", file=sys.stderr)
            time.sleep(2 ** attempt * 2)
            continue
        return r
    raise RuntimeError(f"Gave up on {url}")


def get_json_cached(url, cache_name, max_age_hours=24):
    path = config.JSON_CACHE / cache_name
    if path.exists() and (time.time() - path.stat().st_mtime) < max_age_hours * 3600:
        return json.loads(path.read_text())
    r = get(url)
    r.raise_for_status()
    path.write_text(r.text)
    return r.json()


def get_text_cached(url, cache_path: Path):
    """Archive files never change once filed, so cache forever."""
    if cache_path.exists():
        return cache_path.read_text(errors="replace")
    r = get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(r.text)
    return r.text
