# Return Authenticated Absolute Downloads

## Goal

Give external runtimes a complete, authenticated JSONL handoff without requiring
them to guess Lyra's public base URL or credential behavior.

## Background from the discussion

`lyra_download_result` currently returns a relative path and claims authentication
that the raw route did not enforce. The auth step fixes route protection; this
step makes the handoff operationally complete.

## Scope

- Add a validated public API base URL to runtime configuration.
- Return an absolute HTTPS or explicitly configured local HTTP JSONL URL.
- Describe Bearer authentication and the `LYRA_AGENT_API_KEY` environment
  variable without returning a credential value.
- Keep sync and async Python result downloads authenticated.
- Preserve expiry metadata so agents know when to fetch promptly.

## Out of scope

- Signed URLs, embedded table bytes, object storage, Parquet, and CSV.
- Durable promotion beyond current Redis TTL.
- Browser cookie authentication.

## Files or areas likely affected

- `config.example.toml`
- `lyra_app/config.py`
- `lyra_app/main.py`
- `packages/lyra_api/src/lyra/api/client/`
- MCP server, contract models, and tools.
- Config, MCP, and API-client tests.

## Required behavior

- A successful table handoff includes one absolute URL, JSONL media type,
  Bearer scheme, credential environment-variable name, and expiry.
- URLs are derived only from validated operator configuration, never untrusted
  forwarding headers.
- Production configuration requires `https`; local loopback development may use
  explicit `http`.
- URLs contain no credentials, query tokens, fragments, internal file paths, or
  proxy-only hostnames.
- The advertised URL downloads the exact referenced table with the agent token
  and rejects missing/invalid tokens.
- File, failed, cancelled, and expired results return their existing structured
  unsupported/terminal/expired behavior.

## Implementation notes

- Normalize one trailing-slash convention and join paths without stringly URL
  concatenation bugs.
- Keep the tool name if convenient, but no compatibility alias or dual result
  shape is required.
- Do not teach clients to use the admin credential for result access.

## Tests and verification

- Use the manifest-declared handoff tests.
- Cover production/local URL validation, proxy-header rejection, no secret
  leakage, authenticated download, result-kind errors, and sync/async clients.

## Step exit checklist

- [ ] The handoff contains a valid absolute URL and explicit auth metadata.
- [ ] The URL never contains credentials or internal paths.
- [ ] The advertised URL succeeds only with the agent token.
- [ ] Sync and async clients download the referenced JSONL.

## Decision gate before the next step

Proceed only when an external-style client can use only the handoff plus its
agent environment credential to retrieve the table.

## Next-step context

The final implementation step documents the new security boundary, contracts,
controls, and end-to-end agent workflow.
