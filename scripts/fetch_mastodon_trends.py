"""Fetch trending hashtags from one or more Mastodon servers.

Writes a single JSON document that downstream consumers can read over raw
githubusercontent. Uses only the standard library so the workflow needs no
dependency installation step.

Environment variables:
    MASTODON_SERVERS    Comma-separated hostnames. Default: mastodon.social
    MASTODON_TOKENS     JSON object mapping hostname -> bearer token. Optional.
                        Only needed for servers that disallow unauthenticated
                        API access. A token is NEVER sent to a host other than
                        the one it is mapped to.
    MASTODON_LIMIT      Tags to request per server (1-20). Default: 20
    MASTODON_USER_AGENT User-Agent string. Set this to something identifying;
                        several instances block generic agents.

Usage:
    python fetch_mastodon_trends.py data/trends.json
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TIMEOUT = 15
MAX_RETRIES = 3
BACKOFF_SECONDS = 5
PAUSE_BETWEEN_SERVERS = 2

USER_AGENT = os.environ.get(
    "MASTODON_USER_AGENT", "mastodon-trends/1.0 (+https://github.com/)"
)


class FetchError(Exception):
    """A server could not be queried. Carries a human-readable reason."""


def _request(url: str, token: str | None) -> list:
    """Single HTTP GET returning parsed JSON. Raises urllib.error.HTTPError."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_with_retries(url: str, token: str | None) -> list:
    """GET with retries on 429 and 5xx, honouring Retry-After where present."""
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            return _request(url, token)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = (
                        int(retry_after)
                        if retry_after
                        else BACKOFF_SECONDS * (2**attempt)
                    )
                except ValueError:
                    delay = BACKOFF_SECONDS * (2**attempt)
                print(f"  HTTP {exc.code}, retrying in {delay}s", file=sys.stderr)
                time.sleep(min(delay, 120))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_SECONDS * (2**attempt))
                continue
            raise

    raise FetchError(str(last_error))


def fetch_server(host: str, token: str | None, limit: int) -> list[dict]:
    """Fetch trending tags from one host, falling back to the pre-3.5 path."""
    paths = ["/api/v1/trends/tags", "/api/v1/trends"]
    last_error: Exception | None = None

    for path in paths:
        url = f"https://{host}{path}?limit={limit}"
        try:
            return _get_with_retries(url, token)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 404:
                # Older Mastodon, or a fork without the newer route. Try next.
                continue
            if exc.code in (401, 403):
                hint = (
                    "authentication required (server disallows unauthenticated "
                    "API access) — supply a token in MASTODON_TOKENS"
                    if not token
                    else "token rejected — check it is valid and has the read scope"
                )
                raise FetchError(f"HTTP {exc.code}: {hint}") from exc
            raise FetchError(f"HTTP {exc.code}: {exc.reason}") from exc
        except Exception as exc:
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc

    raise FetchError(f"no trends endpoint found: {last_error}")


def normalise(raw_tags: list[dict]) -> list[dict]:
    """Flatten Mastodon Tag objects into something easier to consume."""
    out = []

    for rank, tag in enumerate(raw_tags, start=1):
        history = tag.get("history") or []

        def _sum(field: str) -> int:
            total = 0
            for entry in history:
                try:
                    total += int(entry.get(field, 0))
                except (TypeError, ValueError):
                    pass
            return total

        def _today(field: str) -> int:
            if not history:
                return 0
            try:
                return int(history[0].get(field, 0))
            except (TypeError, ValueError):
                return 0

        out.append(
            {
                "rank": rank,
                "name": tag.get("name"),
                "url": tag.get("url"),
                "accounts_today": _today("accounts"),
                "uses_today": _today("uses"),
                "accounts_week": _sum("accounts"),
                "uses_week": _sum("uses"),
            }
        )

    return out


def load_tokens() -> dict[str, str]:
    raw = os.environ.get("MASTODON_TOKENS", "").strip()
    if not raw:
        return {}
    try:
        tokens = json.loads(raw)
    except json.JSONDecodeError:
        print(
            'MASTODON_TOKENS is not valid JSON; expected {"host": "token"}. '
            "Continuing without tokens.",
            file=sys.stderr,
        )
        return {}
    if not isinstance(tokens, dict):
        print("MASTODON_TOKENS must be a JSON object. Ignoring.", file=sys.stderr)
        return {}
    return {str(k): str(v) for k, v in tokens.items()}


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} OUTPUT_PATH", file=sys.stderr)
        return 2

    out_path = Path(sys.argv[1])

    servers = [
        s.strip()
        for s in os.environ.get("MASTODON_SERVERS", "mastodon.social").split(",")
        if s.strip()
    ]
    tokens = load_tokens()
    limit = max(1, min(20, int(os.environ.get("MASTODON_LIMIT", "20"))))

    results: dict[str, dict] = {}
    failures = 0

    for index, host in enumerate(servers):
        if index:
            time.sleep(PAUSE_BETWEEN_SERVERS)

        print(f"Fetching {host}", file=sys.stderr)
        try:
            raw_tags = fetch_server(host, tokens.get(host), limit)
        except FetchError as exc:
            failures += 1
            print(f"  failed: {exc}", file=sys.stderr)
            results[host] = {"ok": False, "error": str(exc), "tags": []}
            continue

        tags = normalise(raw_tags)
        print(f"  {len(tags)} tags", file=sys.stderr)
        results[host] = {"ok": True, "error": None, "tags": tags}

    if failures == len(servers):
        print("All servers failed; leaving existing data untouched.", file=sys.stderr)
        return 1

    # Only rewrite the file when the substance changed, so the timestamp alone
    # does not produce a commit on every single run.
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            if existing.get("servers") == results:
                print("No change since last run.", file=sys.stderr)
                return 0
        except (json.JSONDecodeError, OSError):
            pass

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "servers": results,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
