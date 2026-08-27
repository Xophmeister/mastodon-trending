#!/usr/bin/env bash

set -euo pipefail

readonly CLIENT_NAME="mastodon-trending"
readonly REDIRECT_URI="urn:ietf:wg:oauth:2.0:oob"
readonly SCOPES="read:statuses"
readonly GRANT_TYPE="client_credentials"

log() {
  >&2 printf '%s\n' "$*"
}

post() {
  local url="$1"
  shift

  curl \
    -X POST "${url}" \
    --silent --show-error --fail-with-body \
    --connect-timeout 5 --max-time 30 \
    "$@"
}

get-app-tokens() {
  # Get new application client ID and secret for a given Mastodon host.
  # This is required to get a bearer token.
  #
  # NOTE This is not idempotent. If you call this multiple times for the
  # same host, it will create multiple applications.
  local host="$1"

  post "https://${host}/api/v1/apps" \
    --data-urlencode "client_name=${CLIENT_NAME}" \
    --data-urlencode "redirect_uris=${REDIRECT_URI}" \
    --data-urlencode "scopes=${SCOPES}" \
  | jq \
    --arg host "${host}" \
    '{ $host, client_id, client_secret }'
}

get-bearer-token() {
  # Get a bearer token for a given Mastodon host using an application's
  # client ID and secret.
  local host payload
  eval "$(jq -r \
    --arg grant_type "${GRANT_TYPE}" \
    --arg scope "${SCOPES}" '
      def form: to_entries | map("\(.key | @uri)=\(.value | @uri)") | join("&");

      @sh "host=\(.host) payload=\(
        { client_id, client_secret, $grant_type, $scope } | form
      )"
    ')"

  printf '%s' "${payload}" \
  | post "https://${host}/oauth/token" --data @- \
  | jq --arg host "${host}" '{ ($host): .access_token }'
}

main() {
  if ! (( $# )); then
    log "Usage: $0 <MASTODON-HOST> [<MASTODON-HOST> ...]"
    exit 1
  fi

  for host in "$@"; do
    log "Getting tokens for ${host}..."
    get-app-tokens "${host}" | get-bearer-token
  done \
  | jq -s 'add // {}'
}

main "$@"
