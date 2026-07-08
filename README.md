# EndPointBlank (Python)

EndPointBlank client for Python (Django / Flask / any WSGI app) — endpoint tracking, request
authorization, error/request/response/log reporting, and client-side data masking.

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
| `cache_ttl` | — | `300` | Seconds a successful authorization result is cached (keyed on client-auth + path + method). |
| `masking_rules` | — | `[]` | List of masking rule dicts. See [Data masking](#data-masking). |
| `mask_hook` | — | `None` | Optional callable `(payload, record_type) -> payload` run after rule-based masking. |

`Configuration` is a singleton — `Configuration()` always returns the same instance, and you can
also read/assign its attributes directly (`Configuration().log_base_url = "..."`) instead of going
through `configure()`.

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

`end_point_blank.authorization.Authorization` builds the `Authorization` header used on every
outbound call to the EndPointBlank API: a cached `Bearer` token if one exists for the target
hostname, otherwise HTTP Basic auth from `client_id`/`client_secret`.

```python
from end_point_blank.authorization import Authorization

header_value = Authorization.header()               # "Basic <base64(client_id:client_secret)>"
header_value = Authorization.header(hostname="api.example.com")  # "Bearer <token>" if cached
```

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

Successful authorization results are cached in-process for `cache_ttl` seconds (default 300) to
avoid a network round trip on every request.

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
├── authorization.py         # Authorization header builder
├── masking.py               # Client-side masking engine (JSONPath subset + regex)
├── request_store.py         # Thread-local current-request store
├── unauthorized_error.py    # UnauthorizedError
├── log_entry.py             # LogEntry value object
├── middleware/               # WSGI middleware (ReportInteractionMiddleware)
├── writers/                  # RequestWriter, ResponseWriter, ExceptionWriter, LogWriter,
│                              # DirectWriter / DelayedWriter transports
├── commands/                  # HTTP command objects: authorize, authenticate, endpoint
│                              # update, access-token generation, version/route-pattern finders
├── tokens/                    # Access-token cache
├── flask/                     # authenticated/authorized/versioned decorators + endpoint registrar
└── django/                    # middleware, decorators, versioned, endpoint registrar
tests/                        # pytest suite mirroring the src/ layout
```

## License

No `LICENSE` file is currently included in this repository. All rights reserved by the author
(Robert A. Lasch) until a license is added.

## Links

- Repository: https://github.com/EndPointBlank/end_point_blank_py
