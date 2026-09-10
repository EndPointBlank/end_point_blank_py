# Changelog

## 0.9.0

### Fixed

- **`@authenticated` could never succeed.** `BasicAuthenticate` posted the
  request method under the key `action`. Intake reads `http_method`: every
  clause of `AuthorizeAccess.authorize/1` pattern-matches that key, so a body
  without it falls through to the catch-all and the authorize controller renders
  401 `invalid_params`. The credential presented never mattered, because the
  request was rejected before any credential was considered. Any application
  using the Flask or Django `@authenticated` decorator against a real
  EndPointBlank was refused on every request; it now reaches the view.

  Two further keys were sent under names intake does not read. Neither failed
  loudly — they simply wrote nothing:

  | was sent as | is now sent as | what intake does with it | what you saw before |
  | --- | --- | --- | --- |
  | `action` | `http_method` | matches the endpoint being called | 401 on every authenticated request |
  | `version` | `endpoint_version` | records the version, and looks up its deprecation | version recorded as null, and the deprecation lookup asked about no version at all |
  | `ip_address` | `source_ip` | records `source_ip_address` on the authorization | the client address recorded as null on every authentication |

  The deprecation lookup is worth a caveat. Intake now performs it for an
  authenticated call, but the `@authenticated` decorators still do not read the
  `deprecation` block back out of the response the way `@authorized` does, so no
  `Deprecation` or `Sunset` header appears on an authenticated route yet. That
  is a separate gap and is unchanged here.

  Nothing else about the call changed. The `ip_address=` keyword argument on
  `BasicAuthenticate.authenticate` keeps its name — only the name it travels
  under on the wire is different — and `application`, which intake ignores, is
  still sent for parity with `EndpointAuthorize`.

  Deployments that were already succeeding are unaffected, because none were:
  this path returned 401 for every request it ever made. Integrators who worked
  around it by using `@authorized` in place of `@authenticated` can now use the
  decorator they meant.

### Changed

- The names intake reads on `POST /api/authorize` are now pinned in one place,
  `tests/test_intake_param_contract.py`, parametrized over both commands that
  call that endpoint. `BasicAuthenticate` and `EndpointAuthorize` post the same
  fields to the same URL but each spelled the body out for itself, which is how
  one of them came to use three names the other did not. The unit test that
  should have caught this asserted `body["action"] == "GET"` against a mock and
  passed for as long as the bug existed — a double agrees with whatever it is
  handed. `tests/test_authenticate_over_http.py` now drives a real Flask route
  behind `@authenticated` over a loopback socket into a stub that refuses a
  body without `http_method` the way intake does.

## 0.8.0

### Added

- **A refusal now says which refusal it was.** `UnauthorizedError` carries a
  `status_code`, and the `authenticated` / `authorized` decorators pass the
  status intake answered with instead of dropping it:

  ```python
  try:
      ...
  except UnauthorizedError as error:
      return JsonResponse({"error": str(error)}, status=error.status_code)
  ```

  | intake answered | `status_code` | what it tells the integrator |
  | --- | --- | --- |
  | 401 | `401` | the credential was not accepted — re-check or re-issue it |
  | 403 | `403` | the credential is fine; no grant covers this endpoint — ask for one |
  | any other non-201 | that status | intake's own verdict, verbatim |
  | nothing at all | `503` | the check could not be made; nothing judged this caller |

  Every Python refusal used to arrive as a 401, because the exception had
  nowhere to put the status and the raise sites threw it away. 401 and 403 send
  an integrator to two different places, so collapsing them sent half of them to
  debug the wrong thing. The other SDKs each carry the same value under their
  own idiomatic name — `statusCode` in JS, `getStatusCode()` in Java, `status`
  in Ruby, and structurally as `{:error, status, body}` in Elixir; this is
  Python's.

  `UnauthorizedError("message")` is unchanged and still means what it meant:
  the status defaults to 401. The status is the optional second positional
  argument, and it survives a pickle round trip rather than quietly reverting
  to the default on the far side.

### Changed

- **An unreachable intake is a 503, not a 401.** When intake does not answer at
  all, nothing refused the caller and no credential was judged, so blaming the
  credential sent integrators to re-issue one that was fine. This is also what
  the other four SDKs already send for the same case.
- **A refusal is no longer recorded to intake with a null status.** Both the
  Django and the WSGI middleware re-raise `UnauthorizedError` without reporting
  it as an application error — but neither recorded a status for it, because the
  refusal never reaches the response object each one reads its status from. The
  response row went out with `status: null`, which intake rejects, so the row for
  a denied request silently never landed. Both now record the refusing status.
  The synthesized `500` stays where it belongs: on a genuine unhandled
  application error, which is what the caller will actually be served.
- The four decorator refusal sites — Flask and Django x authenticate and
  authorize — were four transcriptions of one decision and now share one
  function, `unauthorized_error.refusal_from`. Four copies is how one site
  acquires a fix the other three do not, which is how the status came to be
  dropped at all four at once.

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
