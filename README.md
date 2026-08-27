# Mastodon trends fetcher

Simple Python script that fetches current trending hashtags from
Mastodon instances and outputs them in a JSON format. This is set up to
run as a scheduled GitHub Action, whenever GitHub deigns to run the
workflow (or you do it manually out of frustration).

## Getting bearer tokens

Run:

```bash
scripts/get-tokens.sh MASTODON_HOST [MASTODON_HOST ...] |
| tee tokens.json
```

where `MASTODON_HOST` is the domain of the Mastodon instance for which
you need bearer tokens for the trends fetcher. The script will output a
JSON file with `MASTODON_HOST` as the key and the bearer token as the
value; this can be copied as the `MASTODON_TOKENS` GitHub secret for the
fetcher action.
