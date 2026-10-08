# EndPointBlank (Python)

Python SDK for [EndPointBlank](https://endpointblank.com) (Django / Flask / any WSGI app):
authorize service-to-service API calls, report endpoint versions, and see which clients still call
deprecated API versions. It covers endpoint tracking, request authorization,
error/request/response/log reporting, and client-side data masking.

## Installation

The package is **not yet published to PyPI**. Install it from source or as a git dependency.

```sh
pip install end-point-blank-py
```

Until it's published, install directly from git:

```sh
pip install "git+https://github.com/EndPointBlank/end_point_blank_py.git"
```

or add it to your `pyproject.toml` / `requirements.txt` as a VCS dependency:

```
end-point-blank-py @ git+https://github.com/EndPointBlank/end_point_blank_py.git
```

Framework extras are available if you want Flask/Django installed alongside it:

```sh
pip install "end-point-blank-py[flask]"
pip install "end-point-blank-py[django]"
```

Requires **Python >= 3.10**.

## Quick start

```python
import end_point_blank as epb

epb.configure(
    client_id="your-client-id",
    client_secret="your-client-secret",
    app_name="my-app",
    environment="production",
)

# Wrap any WSGI app to report requests/responses/errors automatically.
from end_point_blank.middleware import ReportInteractionMiddleware

app = ReportInteractionMiddleware(app)
```

That's it — every request/response passing through the wrapped app is now reported to
EndPointBlank, and unhandled exceptions are captured and sent as error reports.

## Configuration

Call `end_point_blank.configure(...)` once at startup (e.g. app factory / settings module).
Only the parameters you pass are updated; everything else keeps its current value.

A subset of settings also fall back to `ENDPOINTBLANK_*` environment variables so the library
works with zero code changes in 12-factor deployments (e.g. container environments where secrets
are injected as env vars). **Precedence for those settings is:**

```
explicit configure(...) value  >  ENDPOINTBLANK_* environment variable  >  built-in default
```

Precedence is resolved at *read* time (not at import time), so setting the env var after the
process has started but before the first call still takes effect as long as `configure()` wasn't
called with an explicit value for that setting.

| `configure()` keyword | Env var fallback | Default | Notes |
|---|---|---|---|
| `client_id` | `ENDPOINTBLANK_CLIENT_ID` | `None` | Your EndPointBlank client ID. |
| `client_secret` | `ENDPOINTBLANK_CLIENT_SECRET` | `None` | Your EndPointBlank client secret. |
| `base_url` | `ENDPOINTBLANK_BASE_URL` | `https://in.endpointblank.com` | Control-plane API (authorize/authenticate, access tokens, endpoint updates, endpoint errors). |
| — *(no `configure()` kwarg)* | `ENDPOINTBLANK_LOG_BASE_URL` | `https://log.endpointblank.com` | Log-plane API (requests/responses/logs/application errors). Set via env var or `Configuration().log_base_url = ...` directly — not exposed as a `configure()` parameter. |
| `app_name` | `ENDPOINTBLANK_APP_NAME` | `None` | Application name reported to the API. |
| `environment` | `ENDPOINTBLANK_ENV` | `None` | Runtime environment name (e.g. `"production"`). |
| `worker_count` | — | `4` | Number of background worker threads used when `log_mode=LogMode.DELAYED`. |
| `log_mode` | — | `LogMode.DIRECT` | `LogMode.DIRECT` sends synchronously; `LogMode.DELAYED` queues onto background workers. |
| `version_finder` | — | `None` | Optional callable `(environ) -> str \| None` for custom API-version detection, overriding the built-in header/query/path lookup. |
| `application_version` | — | `None` | Overrides the app version sent in endpoint updates. |
| `token_ttl` | — | `None` | Optional access-token TTL (seconds) sent to the token endpoint. |
| `cache_ttl` | — | `300` | Seconds a successful authorization result is cached (keyed on client-auth + path + method). `0` disables the cache. `None`, a negative number or a non-`int` raises `ValueError` — see [`cache_ttl`](#cache_ttl). |
| `trust_proxy_headers` | — | `True` | Whether the per-request `scheme`/`host`/`port` report honors `X-Forwarded-Proto`/`-Host`/`-Port`. See [Reported base URL](#reported-base-url). |
| `masking_rules` | — | `[]` | List of masking rule dicts. See [Data masking](#data-masking). |
| `mask_hook` | — | `None` | Optional callable `(payload, record_type) -> payload` run after rule-based masking. |
| `derive_base_url_from_client_id` | — | `False` | Derive the intake hostname from a slug-prefixed `client_id` when no base URL is set. See [Intake hostname from `client_id`](#intake-hostname-from-client_id). Anything but `True` or `False` raises `ValueError`. |

`Configuration` is a singleton — `Configuration()` always returns the same instance, and you can
also read/assign its attributes directly (`Configuration().log_base_url = "..."`) instead of going
through `configure()`.

### `cache_ttl`

The JS, Java, Elixir, Python and Rails SDKs all follow the same `cache_ttl` rule:

| `cache_ttl` | Meaning |
|---|---|
| omitted | the default, 300 seconds (or the value an earlier `configure()` call set) |
| `0` | the authorization cache is disabled |
| a positive `int` | that many seconds |
| `None` | `ValueError` — omit the argument to get the default |
| a negative number | `ValueError` — use `0` to disable the cache |
| anything not an `int` (`3.5`, `300.0`, `"60"`, `True`) | `ValueError` |

The error is raised by `configure()` itself, before it applies any of its arguments, so a bad
value stops your application at startup rather than on its first `@authorized` request.
Assigning `Configuration().cache_ttl` directly is checked the same way. `cache_ttl` is the only
`configure()` argument where `None` differs from leaving the argument out; every other argument
treats `None` as omitted. `bool` is refused even though Python counts it as an `int`, because
`True` would otherwise mean one second. If the value comes from an environment variable, convert
it with `int(...)` first.

### Reported base URL

Every request payload carries the base URL the *caller* used, as three separate fields —
`scheme`, `host` and `port`. A field that cannot be resolved is omitted rather than sent as
null. EndPointBlank uses these to fill in an application environment's base URL for you,
instead of asking someone to type it.

By default the library honors `X-Forwarded-Proto`, `X-Forwarded-Host` and `X-Forwarded-Port`,
reading the **last** comma-separated hop, straight off the WSGI environ. (WSGI itself has no
notion of a trusted proxy, which is why this library previously could not see through a load
balancer at all.) It resolves them the same way the Ruby, JS, Java and Elixir clients do, so
all five answer identically for the same request.

**Turn this off if your application is reachable directly, with no proxy in front of it** —
or if you would simply rather report nothing than report something a caller could influence:

```python
epb.configure(trust_proxy_headers=False)
```

With it off, the `X-Forwarded-*` headers are ignored entirely and `scheme`, `host` and `port`
come from the connection and the `Host` header only.

It defaults to `True` because the alternative is worse for almost everyone. Most production
deployments sit behind an ALB, nginx, Caddy or an Ingress, and a client that ignored the
forwarded headers there would not report *nothing* — it would confidently report an internal
hostname on an internal port. `host` is caller-controlled either way (it has always come from
`HTTP_HOST`), and none of these three values is ever used as an identity or authorization
key, so the worst case is a wrong *suggestion* that an admin has to approve.

### Intake hostname from `client_id`

Each organization's intake will answer at its own hostname,
`https://<slug>.in.endpointblank.com`, and every new `client_id` starts with that slug and a
dot (`acima-x7k2mq.ijXI+MVwmrC5xH/9ZuGiQlAbAyobTqMa`). With
`configure(derive_base_url_from_client_id=True)`, the library picks its intake in this order:

1. `base_url`, or else `ENDPOINTBLANK_BASE_URL`, if either is set;
2. else, if the `client_id` carries a slug prefix, `https://<slug>.in.endpointblank.com`;
3. else `https://in.endpointblank.com`.

A `client_id` carries a slug prefix only when the part before its first `.` has the exact shape
of an organization slug and something follows the dot
(`end_point_blank.configuration.client_id_slug`). A credential issued before slugs, including
one with a `.` in it such as `my.client`, keeps calling `https://in.endpointblank.com`.

**This is off by default, and turns on by default in a later release, once DNS and TLS for
`*.in.endpointblank.com` are live.** Until then those hostnames do not resolve in production, so
leave it off unless EndPointBlank has told you otherwise. With it off, the base URL is
`base_url`, else `ENDPOINTBLANK_BASE_URL`, else `https://in.endpointblank.com`, whatever the
`client_id`.

The logs hostname is not derived: `log_base_url`, else `ENDPOINTBLANK_LOG_BASE_URL`, else
`https://log.endpointblank.com`, as before.

Every call to intake also sends `x-epb-sdk: python/<version>`, so EndPointBlank can tell which
SDK versions use a credential before it moves an organization to another intake. The minimum
Python version for a move is the release that turns `derive_base_url_from_client_id` on by
default, **not** this one: with the option at its default here, the library keeps calling
`https://in.endpointblank.com` after its organization has moved.

### `configure(...)` example

```python
import end_point_blank as epb
from end_point_blank.configuration import LogMode

epb.configure(
    client_id="your-client-id",
    client_secret="your-client-secret",
    base_url="https://in.endpointblank.com",
    app_name="my-app",
    environment="production",
    log_mode=LogMode.DELAYED,   # send reports from background worker threads
    worker_count=8,
    cache_ttl=600,
)
```

### 12-factor / env-var example

With these set in the environment, you can call `epb.configure()` with no arguments (or only the
values not covered by env vars, e.g. `log_mode`):

```sh
export ENDPOINTBLANK_CLIENT_ID="your-client-id"
export ENDPOINTBLANK_CLIENT_SECRET="your-client-secret"
export ENDPOINTBLANK_BASE_URL="https://in.endpointblank.com"
export ENDPOINTBLANK_LOG_BASE_URL="https://log.endpointblank.com"
export ENDPOINTBLANK_APP_NAME="my-app"
export ENDPOINTBLANK_ENV="production"
```

```python
import end_point_blank as epb

epb.configure()  # picks up all ENDPOINTBLANK_* env vars above
```

## Usage

### Authorization

`end_point_blank.authorization.Authorization.header(base_url)` builds the `Authorization` header
for an outbound call to a provider — another application you are about to call. It returns
`Bearer <token>`, minting a token for that target if none is held, and nothing else.

```python
from end_point_blank.authorization import Authorization
from end_point_blank import TokenUnavailableError

# Pass the URL you are about to call, NOT a hostname.
# userinfo, query and fragment are removed before the token request; they are
# never sent to intake, logged, or kept on the error.
try:
    auth = Authorization.header("https://api.example.com/orders")  # "Bearer <token>"
except TokenUnavailableError as error:
    # No token could be minted. Do not make the call, and do not send Basic
    # credentials instead -- see below.
    logger.warning("%s (outcome=%s)", error, error.outcome)
    raise
```

**Your `client_id`/`client_secret` is never sent to a provider.** When no token can be obtained —
EndPointBlank is unreachable or times out, it rejects the credential (401), the URL resolves to no
registered environment (4xx), or it fails (5xx) — `header` raises
`end_point_blank.TokenUnavailableError` instead of falling back to HTTP Basic. The message says the
token could not be minted and why, in fixed words -- never intake's response body or an exception's
text. `error.base_url` is the URL you asked about, stripped to scheme, host, port and path;
`error.failure` is the outcome and status of the mint made for this call (a `TokenResult` without
its payload), `error.outcome` its `TokenOutcome` and `error.status` its HTTP status (`None` when
nothing answered), so you can decide whether a retry can help (`TRANSPORT_ERROR`, `SERVER_ERROR`)
or not (`CREDENTIAL_REJECTED`, `REQUEST_REJECTED`). A mint that raised is a `TRANSPORT_ERROR`
with `error.unexpected` true, whose exception is `error.cause` (and `__cause__`): a bug or a bad
request (a malformed URL or header), not an unreachable intake. Only a refused or dropped
connection, a timeout or a body cut off mid-stream is reported as intake being unreachable.
There is no no-argument form: calling `header()` with no argument raises `TypeError`, and
`header(None)`, `header("")` or a URL that is not an absolute http or https URL with a host raises
`ValueError` without making a request (the Ruby gem raises `ArgumentError` here). That includes any
other scheme (`ftp`, `ws`, `file` ...) and a port that is not a number in 1..65535.

`header` raises `end_point_blank.ConfigurationError`, not `TokenUnavailableError`, when
`client_id` or `client_secret` is missing or empty: nothing is sent, and retrying cannot help.
The SDK's other calls to its intake refuse to send an empty credential too, and log the error
instead of raising it into your application: endpoint registration logs and carries on,
`@authenticated` / `@authorized` refuse the request with 503, and the writers drop the record.

The argument is the URL you are about to call. Intake matches it against the registered base
URLs by longest path prefix, so you do not need to know how the target registered itself —
`https://api.example.com/orders/widgets/42` resolves to whichever environment owns it. Userinfo,
query and fragment are removed before the token request; they are never sent to intake, logged, or
kept on the error. The scheme and host are lowercased and a default or empty port is dropped, so
`HTTPS://API.Example.com:443/orders` is sent as `https://api.example.com/orders`; the path is kept
as written.

Tokens are cached per application environment, keyed on the canonical base URL intake resolves
the request to (not on the URL you passed), so a service that calls several targets holds a
token for each.

The SDK's own calls to EndPointBlank (authorize, token minting, endpoint registration, the
log/request/response/exception writers) still authenticate with HTTP Basic from
`client_id`/`client_secret`, through an internal helper. That is deliberate — EndPointBlank already
holds this service's credential, so minting a token in order to present it back would buy nothing.

Route/endpoint authorization itself is enforced via the Flask/Django decorators below
(`authenticated`, `authorized`), which call the `commands.basic_authenticate.BasicAuthenticate` and
`commands.endpoint_authorize.EndpointAuthorize` commands under the hood and raise
`end_point_blank.unauthorized_error.UnauthorizedError` on a non-201 response:

```python
from flask import Flask
from end_point_blank.flask import authenticated, authorized

app = Flask(__name__)

@app.route("/protected")
@authenticated
def protected_view():
    return "Hello, authenticated user!"

@app.route("/sensitive")
@authorized
def sensitive_view():
    return "Hello, authorized user!"
```

The error carries intake's own verdict as `status_code`, so your handler can tell the two
refusals apart — 401 means the credential was not accepted, 403 means the credential is fine
but no grant covers this endpoint. They send an integrator to two different places, so they
must not be collapsed:

```python
@app.errorhandler(UnauthorizedError)
def handle_refusal(error):
    return {"error": str(error)}, error.status_code
```

| intake answered | `status_code` |
| --- | --- |
| 401 | `401` — re-check or re-issue the credential |
| 403 | `403` — ask for a grant covering this endpoint |
| any other non-201 | that status, verbatim |
| nothing at all | `503` — the check could not be made |

`UnauthorizedError("message")` still works and defaults to 401; the status is an optional
second argument.

Both decorators `POST` to the same endpoint, `{base_url}/api/authorize`, and send the same
fields under the same names. These are the names EndPointBlank reads; anything else in the body
is ignored:

| field | what it is |
| --- | --- |
| `client_auth` | the caller's own `Authorization` header, verbatim — who is calling *you* |
| `path` | the **route pattern**, not the URL that was called: `/students/{student_id}`, never `/students/42`. See below |
| `http_method` | the request method. Required: a body without it is refused with `401 invalid_params`, whatever credential it carried |
| `endpoint_version` | the version `VersionFinder` detected, or `null`. Drives the deprecation lookup behind the `Deprecation` and `Sunset` headers |
| `source_ip` | the client address, from `X-Forwarded-For` where present and `REMOTE_ADDR` otherwise |

This service's own credential travels in the request's `Authorization` header as Basic, not in
the body.

#### The `path` is the route pattern

EndPointBlank resolves the endpoint row by matching `path` exactly against what your application
registered at startup, so the concrete URL matches nothing. Both decorators handle this for you:
they recover the matched route from Flask's `url_rule` or Django's `resolver_match.route` and
rewrite it, `<int:student_id>` becoming `{student_id}`, which is the same form
`register_flask_endpoints` and `register_django_endpoints` publish.

You do not have to do anything for this. It is documented because when it goes wrong the symptom
is misleading: an unresolvable path is refused as a **grant** failure, so a path problem presents
as a permissions problem. If a parameterized route is refused while a static one on the same
credential succeeds, compare the `path` in the decorator's request against the paths your
registrar published.

When no route matched at all — a 404, or a request context pushed by hand — the decorators fall
back to the concrete path, since sending that is better than sending nothing.

Successful **`@authorized`** results are cached in-process for `cache_ttl` seconds (default 300)
to avoid a network round trip on every request. `@authenticated` never reads or writes this cache:
it calls `BasicAuthenticate`, not `EndpointAuthorize`, so an `@authenticated`-only request has no
effect on it at all. Each entry's validity is re-checked against the *currently* configured
`cache_ttl` on every read, not only the value in effect when it was written: lowering `cache_ttl`
at runtime shortens the remaining life of entries already cached (from their next read), and
raising it never extends an entry past the expiry it was written with.

Setting `cache_ttl` to `0` disables the cache (a negative value is rejected; see
[`cache_ttl`](#cache_ttl)). The trigger for a clear is exactly this:
an `@authorized` request, or a direct `AuthenticationCache().retrieve()` / `.exists()` / `.store()`
call, made **in that process** while `cache_ttl` is disabled. When that happens, it clears the
**entire** cache for that process — every cached entry, not only the one being looked up or
written — and a `store()` performed while disabled inserts nothing.

The cache is per process, not shared, and so is this trigger. Under a multi-worker server
(gunicorn, uWSGI, several app processes behind a load balancer, …) each worker holds its own
cache: a disabled `@authorized` request or direct cache call in one worker clears only that
worker's cache, not the others'. A disable followed by a re-enable with **no** `@authorized`
request or direct cache call landing on a given worker in between flushes nothing on that worker,
since nothing there ever observed the disabled state. There is no single action that flushes every
worker; if you need that, call `AuthenticationCache().clear()` in each process yourself.

A grant also names the service that called you. `@authorized` reads
`data[0].source_application_environment_id` from EndPointBlank's `201` and stores it on the
request, and the response, log and error rows written for that request carry it. That is what
lets EndPointBlank show which client an error came from. The id is cached with the rest of the
result, so a cached request is named too. A `201` that carries no id still reaches your view, but
the SDK logs an error, once per uncached authorization, instead of recording the request as if
it had no caller.

The calling organization's EndPointBlank id comes with it: `RequestStore.get_source_organization_id()`
returns `data[0].source_organization_id`, cached beside the environment id, so a cached request has
it too. It is `None`, without a log line, when EndPointBlank is older than that field or the
organization has no id there. Both ids are cleared before each authorization and when a request
arrives, so a refused or failed authorization never names an earlier caller.

### Error, request/response, and log reporting

`ReportInteractionMiddleware` (WSGI) or its Django equivalent automatically:

- stores the current request `environ` in a thread-local (`RequestStore`), keyed by a UUID taken
  from `X-Request-Id` if present, else a generated one;
- sends the request payload via `RequestWriter` before the app runs;
- sends the response payload via `ResponseWriter` after the app runs (in a `finally`, so it fires
  even on error);
- sends unhandled exceptions via `ExceptionWriter` (re-raising `UnauthorizedError` without
  reporting it, since unauthorized access is expected/normal traffic).

```python
from end_point_blank.middleware import ReportInteractionMiddleware

app = ReportInteractionMiddleware(app)  # wraps any WSGI callable
```

You can also call the writers directly, e.g. from a background job or a non-HTTP context:

```python
from end_point_blank.writers.exception_writer import ExceptionWriter
from end_point_blank.writers.log_writer import LogWriter

try:
    risky_call()
except Exception as exc:
    ExceptionWriter.write(exc)
    raise

LogWriter.info("Payment processed", {"amount": 42})
LogWriter.warn("Slow query", {"duration_ms": 812})
LogWriter.error("Database timeout")
LogWriter.fatal("Out of memory")
```

Reports are sent synchronously (`LogMode.DIRECT`, the default) or queued onto background worker
threads (`LogMode.DELAYED`, `worker_count` workers) depending on configuration. All writer failures
are caught and logged internally (via the standard `logging` module) — they never raise into your
application.

### Endpoint registration (Flask / Django)

Publish your route list (and any declared API versions) to EndPointBlank so it can associate
incoming traffic with known endpoints:

```python
# Flask — call once after the app is fully configured.
from end_point_blank.flask import register_flask_endpoints, versioned

@app.route("/api/v1/users")
@versioned(["v1", "v2"], state="Current")
def list_users():
    return []

with app.app_context():
    register_flask_endpoints(app)
```

```python
# Django — call once in AppConfig.ready().
from end_point_blank.django import register_django_endpoints, versioned

@versioned(["v1"], state="Deprecated")
def user_list(request):
    ...

class MyAppConfig(AppConfig):
    def ready(self):
        register_django_endpoints()
```

`@versioned(versions, state="__default__")` can be stacked multiple times on the same view to
declare more than one lifecycle state (e.g. `"Current"` vs. `"Deprecated"`).

### Data masking

Mask sensitive data **before it leaves your app**. Configure an ordered list of rules; each rule
targets one field and masks by a JSONPath, a regex, or both. (The server-side intake also masks
independently, so this is defense in depth, not a replacement for it.)

```python
import end_point_blank as epb

epb.configure(
    masking_rules=[
        # Replace any "ssn" field at any depth in the request body.
        {"target": "request_body", "path": "$..ssn", "replacement_value": "***"},
        # Keep first/last 4 of a card number in error messages via backreferences.
        {"target": "error_message", "regex": r"(\d{4})-\d{4}-\d{4}-(\d{4})", "replacement_value": "$1-****-****-$2"},
    ],
    # Optional: runs after the rules; last chance to transform the payload.
    mask_hook=lambda payload, record_type: payload,
)
```

Rules are plain dicts.

**Rule fields**

- `target` — exactly one of `"request_body"`, `"request_headers"`, `"path"`, `"response_body"`,
  `"error_message"`.
- `path` — an optional JSONPath (supported subset: `$`, `.name`, `['name']`, `[n]`, `.*` / `[*]`,
  and `..name` for recursive descent). Keys are case-sensitive.
- `regex` — an optional regular expression.
- `replacement_value` — the replacement string (default `"..."`).
- `enabled` — optional bool (default `True`); set `False` to keep a rule defined but skip it.

**Semantics — path scopes, regex matches within.** With only a `path`, the selected node is
replaced entirely. With only a `regex`, every matching string is replaced. With both, the regex is
applied only within the path-selected node(s). When a `regex` is present, `replacement_value`
supports backreferences: `$1`, `$2`, … insert capture groups (`$0` the whole match; `$$` for a
literal `$`; an out-of-range or non-participating group expands to `""`).

Masking never raises: an uncompilable regex, a blank/malformed/unsupported path, a non-JSON body,
or a missing/`None` field all degrade to a no-op. Stacktraces and log messages are never masked.

**Credential and cookie headers are never sent.** Before any rule runs, `RequestWriter` drops
`Authorization`, `Proxy-Authorization` and `Cookie` from the request record, and `ResponseWriter`
drops `Set-Cookie` from the response record, whatever their letter case. They are left out of the
record, not masked: they are not in the payload the rules and hook receive. The list is
`SENSITIVE_HEADERS` in `end_point_blank.sensitive_headers`.

## Management API

`end_point_blank.management.ManagementClient` calls the EndPointBlank organization management
API (`/api/v1` on the portal), so a script or service can set up an organization without the
portal: its API packages, clients, package assignments and direct grants, applications,
environments and runtime credentials, and the clients you manage for your customers.

It is separate from the runtime SDK above. It is configured only by its constructor (never by
`configure()` or an `ENDPOINTBLANK_*` variable), and the only credential it sends is a
**management API key** (`epb_mk_...`, created in the portal), as `Authorization: Bearer <key>`,
to the portal. Your runtime `client_id`/`client_secret` are never sent to `/api/v1`, and the
management key is never sent to intake. The key is never shown in an error, a log line or the
client's `repr`.

```python
import os
from end_point_blank.management import ManagementClient

mgmt = ManagementClient(
    key=os.environ["EPB_MANAGEMENT_KEY"],    # epb_mk_...; anything else raises ConfigurationError
    # base_url="https://app.endpointblank.com",  # the default
)

org = mgmt.get_organization()
print(org.name, org.key.scope)               # "read" or "write"
```

### Lists and pagination

Every list has a one-page call returning a `Page` (`items` and `next_cursor`; `limit` is 1-100,
default 50 on the server) and an `iter_*` generator that fetches page after page as you go:

```python
page = mgmt.list_clients(limit=20)
for client in page:
    print(client.name, client.status)
if page.next_cursor:
    page = mgmt.list_clients(limit=20, after=page.next_cursor)

for package in mgmt.iter_api_packages():     # every page
    print(package.id, package.name)
```

### Invite a client and assign a package

The key's organization is the target (the provider); the client it invites is the source.

```python
package = mgmt.create_api_package("Orders read")
endpoint = mgmt.list_endpoints(version="1.0.0", limit=1).items[0]
production = next(e for e in mgmt.iter_environments() if e.production)
mgmt.add_package_endpoint(
    package.id,
    application_id=endpoint.application_id,
    endpoint_id=endpoint.id,                 # omit for every endpoint of the application
    environment_id=production.id,
)

client = mgmt.create_client(
    "Globex",
    contacts=[{"email": "dev@globex.example", "first_name": "Ada", "last_name": "Lee"}],
)
print(client.invite_code)                    # send this to the client; it accepts in the portal

# Before the client accepts this is recorded as "pending" and applied when it does.
assignment = mgmt.assign_package(client.id, api_package_id=package.id, environment_id=production.id)
```

Packages and grants can also be set up in the invite itself:
`create_client(name, packages=[{"api_package_id": ..., "environment_id": ...}], grants=[...])`.

### Credentials

```python
app = mgmt.create_application("Orders", {production.id: "https://orders.example"})
app_env = mgmt.list_application_environments(app.id).items[0]
credential = mgmt.create_credential(app_env.id)
store_secret(credential.client_id, credential.client_secret)   # shown this once

rotated = mgmt.rotate_credential(credential.id)  # new secret; the old one works for the grace window
store_secret(rotated.client_id, rotated.client_secret)

mgmt.revoke_credential(credential.id)
```

`client_secret` is set only on the answer to a create or a rotate, is left out of the
credential's `repr`, and is never logged. Reads (`get_credential`, `list_credentials`) return
metadata only.

### Managed clients

A managed client is a client organization you create and run for your customer until they
claim it. `for_managed_client(id)` gives the same application, environment and credential calls,
sent under `/api/v1/clients/<id>/...`:

```python
# owner_email (optional) names the person at your customer who will own it;
# change it later with mgmt.update_client(managed.id, owner_email=...).
managed = mgmt.create_managed_client("Initech", owner_email="owner@initech.example")
initech = mgmt.for_managed_client(managed.id)

env = initech.create_environment("production-eu", "eu.initech.example")
app = initech.create_application("Initech billing", {env.id: "https://billing.initech.example"})
app_env = initech.list_application_environments(app.id).items[0]
credential = initech.create_credential(app_env.id)        # hand this to your customer's service

mgmt.assign_package(managed.id, api_package_id=package.id, environment_id=production.id)
mgmt.send_claim_invite(managed.id, "owner@initech.example",
                       return_to="https://app.example.com/welcome")   # optional

# Until they claim it, send its owner into its EndPointBlank portal from your
# app: mint a link when they click and redirect their browser to it. The link
# works once and expires after 60 seconds, so never render it into a page, and
# mint a new one (with a new Idempotency-Key, the default) on every click.
session = mgmt.create_portal_session(managed.id,
                                     return_url="https://app.example.com/welcome")  # optional
redirect(session.url)
```

`return_to` is optional: once the customer claims the account, EndPointBlank sends their
browser there. It must equal, byte for byte, a claim return URL your organization registered in
EndPointBlank; anything else is refused with 422 `return_to_not_registered`
(`RequestRefusedError`). Without it, nothing is sent and the customer stays in EndPointBlank.

`create_portal_session` answers a `PortalSession` (`client_id`, `url`, `expires_at`,
`return_url`; `url` is left out of its `repr`). `return_url` must likewise be one of your claim
return URLs. It is refused with `not_found` for a client that is not yours, and with 422
`client_not_managed`, `client_being_removed`, `owner_email_missing` (set one with
`update_client`) or `return_url_not_registered`. The answer is never replayed: a reused
Idempotency-Key raises `IdempotencyReplayUnavailableError`.

Once the customer claims it, the `initech` calls answer `not_found`. A managed client that still
holds credentials can't be deleted: revoke them first.

### Errors

Every failure raises `ManagementApiError` (or a subclass) with `code`, `message`, `details`,
`status` and, on a 429, `retry_after`. Match on `code` -- `ErrorCode` lists the documented
codes, and a code this version does not know is still raised with the code as sent:

```python
from end_point_blank.management import (
    ErrorCode, ManagementApiError, NotFoundError, PlanLimitError, ValidationFailedError,
)

try:
    mgmt.assign_package(client.id, api_package_id=package.id, environment_id=staging.id)
except ValidationFailedError as e:
    print(e.details)                          # {"field": ["message", ...]}
except PlanLimitError:
    ...                                       # 402: upgrade the plan
except ManagementApiError as e:
    if e.code == ErrorCode.NOTHING_PUBLISHED_IN_ENVIRONMENT:
        print(e.message, e.details["published_in"])
    elif e.code == "already_assigned":
        ...
    else:
        raise
```

| Class | Codes |
|---|---|
| `AuthenticationError` (401) | `missing_key`, `invalid_key`, `runtime_credential_refused` |
| `PlanLimitError` (402) | `plan_limit` |
| `InsufficientScopeError` (403) | `insufficient_scope` (a read key tried to write) |
| `NotFoundError` (404) | `not_found` |
| `ConflictError` (409) | `idempotency_request_in_progress`, `intake_credential`, `grant_revoked_concurrently` |
| `IdempotencyReplayUnavailableError` (409) | `idempotency_replay_unavailable` |
| `ValidationFailedError` (422) | `validation_failed` |
| `RequestRefusedError` (422) | every other 422 refusal (`has_dependents`, `already_assigned`, `delete_refused`, ...) |
| `RateLimitedError` (429) | `rate_limited` |
| `ServerError` / `ServiceUnavailableError` (5xx / 503) | `internal_server_error` / `audit_unavailable`, `intake_unavailable` |
| `BadRequestError` (400) | `invalid_pagination`, `invalid_idempotency_key`, `bad_request` |
| `ManagementConnectionError` | `connection_error`: no response arrived |

### Retries and idempotency

Every POST sends an `Idempotency-Key` header -- a generated UUID, or the `idempotency_key=` you
pass -- and the same key on each automatic retry, so a retried POST runs at most once. A call is
retried up to `max_retries` times (default 2; `0` turns retries off):

- a 429 after its `Retry-After` (a wait longer than `max_retry_wait`, default 60 seconds, raises
  `RateLimitedError` instead);
- a 5xx or a request that never got an answer, with backoff, for GET, DELETE and POST -- never
  for PATCH;
- a POST answered `idempotency_request_in_progress`, with the same key.

A DELETE whose first attempt took effect but whose answer was lost raises `NotFoundError` on
its retry: the resource is gone either way.

A credential create or rotate retried after it already succeeded is answered
`idempotency_replay_unavailable`: the secret is shown only once, so it is raised as
`IdempotencyReplayUnavailableError` and never retried. Read the credential (`error.location`
names it) and rotate it if the secret was lost.

## Framework integration

### WSGI (any framework)

```python
from end_point_blank.middleware import ReportInteractionMiddleware

app = ReportInteractionMiddleware(app)
```

### Flask

```python
from flask import Flask
from end_point_blank.middleware import ReportInteractionMiddleware
from end_point_blank.flask import authenticated, authorized, versioned, register_flask_endpoints

app = Flask(__name__)
app.wsgi_app = ReportInteractionMiddleware(app.wsgi_app)

@app.route("/api/v1/users")
@versioned(["v1"], state="Current")
@authorized
def list_users():
    return []

with app.app_context():
    register_flask_endpoints(app)
```

### Django

```python
# settings.py
MIDDLEWARE = [
    "end_point_blank.django.ReportInteractionMiddleware",
    # ... your other middleware
]
```

```python
# views.py
from end_point_blank.django import authenticated, authorized, versioned

@versioned(["v1", "v2"], state="Current")
@authorized
def user_list(request):
    ...
```

```python
# apps.py
from django.apps import AppConfig
from end_point_blank.django import register_django_endpoints

class MyAppConfig(AppConfig):
    def ready(self):
        register_django_endpoints()
```

The Django middleware also implements `process_exception`, so errors are still reported even when
an outer error-rendering middleware converts the exception into a normal response before it would
otherwise reach `ReportInteractionMiddleware.__call__`'s exception handler.

## Development

Clone the repo, then create a virtualenv and install the package in editable mode with dev
dependencies (the bundled `build.sh` does this for you):

```sh
./build.sh
# or manually:
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Run the test suite (the bundled `test.sh` uses `.venv` automatically if present):

```sh
./test.sh
# or:
python -m pytest
```

### Layout

```
src/end_point_blank/
├── __init__.py              # configure(...) + public API surface
├── configuration.py         # Configuration singleton + LogMode
├── authorization.py         # Authorization header builder (Bearer only for providers)
├── masking.py               # Client-side masking engine (JSONPath subset + regex)
├── sensitive_headers.py     # SENSITIVE_HEADERS: never sent in a request or response record
├── request_store.py         # Thread-local current-request store
├── unauthorized_error.py    # UnauthorizedError
├── configuration_error.py   # ConfigurationError (client_id/client_secret missing)
├── token_unavailable_error.py # TokenUnavailableError (no token for a provider call)
├── strip_url.py             # strip_url: drops userinfo/query/fragment before a token request
├── log_entry.py             # LogEntry value object
├── middleware/               # WSGI middleware (ReportInteractionMiddleware)
├── writers/                  # RequestWriter, ResponseWriter, ExceptionWriter, LogWriter,
│                              # DirectWriter / DelayedWriter transports
├── commands/                  # HTTP command objects: authorize, authenticate, endpoint
│                              # update, access-token generation, version/route-pattern finders
├── tokens/                    # Access-token cache, one entry per application environment
├── management/                # ManagementClient for the organization management API (/api/v1)
├── flask/                     # authenticated/authorized/versioned decorators + endpoint registrar
└── django/                    # middleware, decorators, versioned, endpoint registrar
tests/                        # pytest suite mirroring the src/ layout
```

## License

No `LICENSE` file is currently included in this repository. All rights reserved by the author
(Robert A. Lasch) until a license is added.

## Links

- Repository: https://github.com/EndPointBlank/end_point_blank_py
