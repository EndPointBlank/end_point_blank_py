# Changelog

## 0.7.0

### Added

- **A mint failure now says *why*.** `GenerateAccessToken.token_result(base_url)`
  returns a frozen `TokenResult` — `outcome`, `status`, `payload` — where
  `outcome` is a `TokenOutcome`:

  | outcome | status | meaning |
  | --- | --- | --- |
  | `SUCCESS` | 2xx | a token really was minted: the body parsed, and carries a non-empty `token` and the non-empty `base_url` to key it under |
  | `CREDENTIAL_REJECTED` | 401 | permanent; the credential must be re-issued |
  | `REQUEST_REJECTED` | any other 4xx | permanent; the environment is not registered, or the request was malformed |
  | `SERVER_ERROR` | 5xx, or the real 2xx | transient; a 5xx, or a 2xx the SDK cannot use — a body that would not parse, a body that is not a JSON object, no `token`, or a `token` with no `base_url` |
  | `TRANSPORT_ERROR` | — | no usable HTTP status was obtained at all: network failure, timeout, retries exhausted |

  Outcomes are decided by the **status first, body second**: a 401 whose body
  is not JSON — a proxy or WAF answering with an HTML error page — is still
  `CREDENTIAL_REJECTED`, never `TRANSPORT_ERROR`. The status alone classifies
  every non-2xx; only on a 2xx does the body decide anything, and the only
  thing it decides is whether a token was minted. A `SUCCESS` whose token
  turned out to be absent would leave every caller a second check to remember,
  and that is the check that gets forgotten — so it is not a success. The
  parsed body is kept on the result either way, including on a 2xx classified
  `SERVER_ERROR`, so nothing that used to read it stops working.

  There is deliberately no "should I retry?" boolean; callers branch on the
  outcome names, because the remedies differ and a boolean would collapse five
  honest names into two. `TokenOutcome` and `TokenResult` are exported from the
  package root.

- **`AccessTokens().last_failure(base_url)`** returns the most recent failure
  covering that URL, or `None`. It is cleared by a successful mint and by
  `clear()`. `token()` answers `None` for every kind of failure, so this is how
  a caller tells "re-issue the credential" from "wait for intake to come back":

  ```python
  if tokens.token(url) is None:
      failure = tokens.last_failure(url)
      if failure and failure.outcome is epb.TokenOutcome.CREDENTIAL_REJECTED:
          ...  # backing off achieves nothing; the credential is dead
  ```

### Changed

- A rejected credential is logged on its own line, naming the remedy, instead
  of sharing the generic `Failed to generate access token …` line with an
  intake outage.
- A non-2xx response whose body happens to look like a token response is no
  longer cached. It was never reachable against a working intake, but the
  cache is now gated on the classified outcome rather than on the shape of the
  body.
- The generic failure log leads with the HTTP status (`HTTP 422: Missing
  target application`) rather than the message alone, and a 2xx that minted
  nothing says which way it was useless — `HTTP 201 with an unreadable body`,
  `HTTP 201 with no usable body`, `response carried a token but no base_url`,
  `no token in response`.
- A 2xx whose body is valid JSON but not an object — an array or a bare string
  from something in front of intake — is classified rather than raised. It used
  to reach `payload.get(...)` and throw `AttributeError` into the calling
  application's request.

### Changed

**`GenerateAccessToken.token()` now answers `None` unless a token was actually
minted.** It previously returned the parsed body of any response that parsed —
an `{"error": ...}` document from a 401 or 422, or a 2xx that parsed into
something with no usable token in it. Each of those handed the caller a truthy
value for a request that produced no token, which is the failure
`token_result()` was added to remove, one layer down.

This aligns all five SDKs with Elixir, whose equivalent has always answered nil
for anything that was not a mint.

**Upgrade note:** nothing in this package calls `token()` — `AccessTokens`
reads `token_result(base_url).payload` — so no log line or diagnostic changes.
A caller that read an error out of the return value should call
`token_result()` instead: `.payload` is exactly what `token()` used to hand
back, now alongside the outcome that explains it. A caller that only ever read
`["token"]` needs no change, because a body without a usable token was never
something it could act on.

`AccessTokens().token()` and `AccessTokens().exists()` are unchanged — same
arguments, same return values.

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
