#!/usr/bin/env python

"""
Fetch trending hashtags from one or more Mastodon servers.

Writes a single JSON document that downstream consumers can read over
raw githubusercontent. Uses only the standard library so the workflow
needs no dependency installation step.

Environment variables:
  MASTODON_TOKENS      REQUIRED A JSON object mapping hostname to bearer
                       token, e.g., {"mastodon.social": "abc123"}. Its
                       keys are the set of servers to query, so there is
                       no separate server list; every server must have a
                       token. A token is NEVER sent to a host other than
                       the one it is mapped to.
  MASTODON_LIMIT       Tags to request per server (1-20) [default: 20]
  MASTODON_USER_AGENT  User-Agent string. Set this to something
                       identifying; several instances block generic
                       agents.

Usage:
  scripts/fetch_mastodon_trends.py data/trends.json
"""

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


class ConfigError(Exception):
    """The environment is unusable. Carries a human-readable reason."""


class FetchError(Exception):
    """A server could not be queried. Carries a human-readable reason."""


def _request(url: str, token: str) -> list:
    """
    Single HTTP GET returning parsed JSON.
    Raises urllib.error.HTTPError.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )

    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_with_retries(url: str, token: str) -> list:
    """
    GET with retries on 429 and 5xx, honouring Retry-After where present.
    """
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


def fetch_server(host: str, token: str, limit: int) -> list[dict]:
    """
    Fetch trending tags from one host, falling back to the pre-3.5 path.
    """
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
                raise FetchError(
                    f"HTTP {exc.code}: token rejected: check it is valid, "
                    "unexpired, and carries the read scope"
                ) from exc

            raise FetchError(f"HTTP {exc.code}: {exc.reason}") from exc

        except Exception as exc:
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc

    raise FetchError(f"no trends endpoint found: {last_error}")


def _sum(history: list[dict], field: str) -> int:
    """
    Total a history field across the whole window, skipping junk entries.
    """
    total = 0
    for entry in history:
        try:
            total += int(entry.get(field, 0))
        except (TypeError, ValueError):
            pass

    return total


def _today(history: list[dict], field: str) -> int:
    """
    Read a history field from the most recent entry, which Mastodon puts
    first.
    """
    if not history:
        return 0

    try:
        return int(history[0].get(field, 0))

    except (TypeError, ValueError):
        return 0


def normalise(raw_tags: list[dict]) -> list[dict]:
    """
    Flatten Mastodon Tag objects into something easier to consume.
    """
    out = []

    for rank, tag in enumerate(raw_tags, start=1):
        # Not .get("history", []): the default only fires on a missing
        # key, so a present-but-null history would slip through as None.
        history = tag.get("history") or []

        out.append(
            {
                "rank": rank,
                "name": tag.get("name"),
                "url": tag.get("url"),
                "accounts_today": _today(history, "accounts"),
                "uses_today": _today(history, "uses"),
                "accounts_week": _sum(history, "accounts"),
                "uses_week": _sum(history, "uses"),
            }
        )

    return out


def load_tokens() -> dict[str, str]:
    """
    Parse MASTODON_TOKENS, which doubles as the list of servers to query.

    A token is mandatory for every server. Some instances do serve
    trends anonymously, but polling them that way is traffic an admin
    can neither attribute nor rate-limit sensibly, so their only
    recourse is to block it.  An application token makes this fetcher a
    good citizen, and costs nothing beyond the client-credentials
    exchange described in README.md.
    """
    raw = os.environ.get("MASTODON_TOKENS", "").strip()
    if not raw:
        raise ConfigError(
            "MASTODON_TOKENS is required and must list every server to query: "
            '{"mastodon.social": "abc123", ...}'
        )

    try:
        tokens = json.loads(raw)

    except json.JSONDecodeError as exc:
        # The exception text describes the position only, never the
        # document, so this cannot leak a token into the log.
        raise ConfigError(f"MASTODON_TOKENS is not valid JSON: {exc}") from exc

    if not isinstance(tokens, dict):
        raise ConfigError('MASTODON_TOKENS must be a JSON object: {"host": "token"}')

    cleaned: dict[str, str] = {}
    for raw_host, token in tokens.items():
        host = str(raw_host).strip().strip("/")
        if not host:
            raise ConfigError("MASTODON_TOKENS contains an empty hostname")

        if "://" in host or "/" in host:
            raise ConfigError(
                f"MASTODON_TOKENS key {host!r} must be a bare hostname, no scheme "
                "or path"
            )

        if not isinstance(token, str) or not token.strip():
            raise ConfigError(f"MASTODON_TOKENS has no usable token for {host!r}")

        cleaned[host] = token.strip()

    if not cleaned:
        raise ConfigError("MASTODON_TOKENS lists no servers; at least one is required")

    return cleaned


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} OUTPUT_PATH", file=sys.stderr)
        return 2

    out_path = Path(sys.argv[1])

    try:
        tokens = load_tokens()
    except ConfigError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    servers = list(tokens)
    limit = max(1, min(20, int(os.environ.get("MASTODON_LIMIT", "20"))))

    results: dict[str, dict] = {}
    failures = 0

    for index, host in enumerate(servers):
        if index:
            time.sleep(PAUSE_BETWEEN_SERVERS)

        print(f"Fetching {host}", file=sys.stderr)
        try:
            raw_tags = fetch_server(host, tokens[host], limit)

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

    # Only rewrite the file when the substance changed, so the timestamp
    # alone does not produce a commit on every single run.
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
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
