## Getting a Mastodon bearer token

```bash
curl -X POST https://<MASTODON-HOST>/api/v1/apps \
  -d client_name="trends-fetcher" \
  -d redirect_uris="urn:ietf:wg:oauth:2.0:oob" \
  -d scopes="read:statuses" \
| jq .
```

then, replacing the `client_id` and `client_secret` values in the
following command with the values returned from the previous command:

```bash
curl -X POST https://<MASTODON-HOST>/oauth/token \
  -d client_id="..." \
  -d client_secret="..." \
  -d grant_type="client_credentials" \
  -d scope="read:statuses" \
| jq .
```

This will return a JSON object containing an `access_token` field, which
is your bearer token. You can use this token to authenticate requests to
the Mastodon API.
