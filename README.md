# Mastodon trends fetcher

Simple Python script that fetches current trending hashtags from
Mastodon instances and outputs them in a JSON format. This is set up to
run as a scheduled GitHub Action, whenever GitHub deigns to run the
workflow (or you do it manually out of frustration).

## Configuration

`MASTODON_TOKENS` is the only configuration the fetcher needs, set as a
repository secret. It is a JSON object mapping each server's bare
hostname to a bearer token:

```json
{ "mastodon.social": "abc123", "fosstodon.org": "def456" }
```

Its keys double as the set of servers to query, so adding a server means
adding its token; there is no separate server list. A token is required
for every server, including those that serve trends anonymously:
unauthenticated polling is traffic an instance admin can neither
attribute nor rate-limit sensibly, leaving a blanket block as their only
recourse. An application token costs nothing and makes the fetcher a
good citizen of the API. A token is never sent to any host other than
the one it is mapped to.

The fetcher exits non-zero, without touching the data, if the secret is
missing or malformed.

## Getting bearer tokens

Run:

```bash
scripts/get-tokens.sh MASTODON_HOST [MASTODON_HOST ...] \
| tee tokens.json
```

where `MASTODON_HOST` is the domain of the Mastodon instance for which
you need bearer tokens for the trends fetcher. The script will output a
JSON file with `MASTODON_HOST` as the key and the bearer token as the
value; this can be copied as the `MASTODON_TOKENS` GitHub secret for the
fetcher action.
