# Changelog

## 0.7.0

### Added

- **A mint failure now says *why*.** `GenerateAccessToken.token_result(base_url)`
  returns a frozen `TokenResult` — `outcome`, `status`, `payload` — where
  `outcome` is a `TokenOutcome`:

  | outcome | status | meaning |
  | --- | --- | --- |
  | `SUCCESS` | 2xx | the payload is the mint |
  | `CREDENTIAL_REJECTED` | 401 | permanent; the credential must be re-issued |
  | `REQUEST_REJECTED` | any other 4xx | permanent; the environment is not registered, or the request was malformed |
  | `SERVER_ERROR` | 5xx | transient; retrying is reasonable |
  | `TRANSPORT_ERROR` | — | no usable response: network failure, timeout, retries exhausted, or an unparseable 2xx body |

  `TokenResult.retryable` answers the question most callers actually have; it
  is `False` for every 4xx, 401 included. `TokenOutcome` and `TokenResult` are
  exported from the package root.

- **`AccessTokens().last_failure(base_url)`** returns the most recent failure
  covering that URL, or `None`. It is cleared by a successful mint and by
  `clear()`. `token()` answers `None` for every kind of failure, so this is how
  a caller tells "re-issue the credential" from "wait for intake to come back":

  ```python
  if tokens.token(url) is None:
      failure = tokens.last_failure(url)
      if failure and not failure.retryable:
          ...  # backing off achieves nothing
  ```

### Changed

- A rejected credential is logged on its own line, naming the remedy, instead
  of sharing the generic `Failed to generate access token …` line with an
  intake outage.
- A non-2xx response whose body happens to look like a token response is no
  longer cached. It was never reachable against a working intake, but the
  cache is now gated on the status rather than on the shape of the body.
- The generic failure log leads with the HTTP status (`HTTP 422: Missing
  target application`) rather than the message alone.

`GenerateAccessToken.token()`, `AccessTokens().token()` and
`AccessTokens().exists()` are unchanged — same arguments, same return values,
including `token()` still handing back the parsed body of a non-2xx response.
All of the above is additive.

## 0.6.0

### Breaking

- **`Authorization.header()` and `AccessTokens().token()` now take a URL, not
  a hostname.** Pass the URL you are about to call —
  `https://api.example.com/orders`, not `api.example.com`. Strip any query
  string or fragment first; they are rejected. Earlier READMEs showed the
  hostname form; those examples no longer work.
- **`AccessTokens().exists()` now requires the same URL argument.** It
  answers for the entry covering that URL; there is no longer a single
  process-wide token for it to answer about.
- **Requires an intake that accepts `base_url`.** An older intake returns
  `400 {"error":"Missing required parameter: base_url"}`.

### Changed

- `endpoint_authorize` authenticates to intake with Basic instead of minting
  an access token for itself. The inbound request path no longer touches the
  token cache at all.
- A 401 from the authorize endpoint is returned to the caller rather than
  retried once. With Basic, a 401 means the credential is wrong.
- Tokens are cached per application environment, keyed on the canonical base
  URL intake resolves the request to, rather than one per process.

### Fixed

- `lib_version` on endpoint-update payloads had been reporting a stale
  `0.2.2` for the last two releases, because `commands/endpoint_update.py`
  carried its own hardcoded `VERSION` literal instead of using the package's
  real version. It now reports the actual installed version. If you relied on
  the portal's "which SDK build is this customer running" data, readings from
  affected releases were wrong.
