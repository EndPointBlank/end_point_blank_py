# P2 #14 — HTTP timeout hardening (Python client library)

Branch: `harden-timeouts`

## Gap

`src/end_point_blank/commands/_http.py` used a single coarse timeout
(`_TIMEOUT = 8`) passed straight to `requests.Session.post(..., timeout=_TIMEOUT)`.
A single scalar timeout in `requests` covers *both* the connect phase and the
read phase with the same budget, so a slow-to-connect but fast-to-respond
intake host (or vice versa) gets the wrong trade-off. The Elixir sibling
library already splits this into connect (3s) + read (5s); this brings the
Python client in line.

## Send-site audit

Traced every command module to find where an HTTP request actually leaves
the process:

| File | Sends HTTP? | How |
|---|---|---|
| `commands/_http.py` | **Yes — the only send site** | `_session().post(...)` |
| `commands/basic_authenticate.py` | No | calls `_http.post()`; `import requests as req_lib` used only for the `Optional[req_lib.Response]` type hint |
| `commands/endpoint_authorize.py` | No | calls `_http.post()` (twice: initial + one retry after evicting a stale cached access token); `req_lib` again type-hint only |
| `commands/generate_access_token.py` | No | calls `_http.post()` |
| `commands/endpoint_update.py` | No | calls `_http.post()` |

**Conclusion: there is exactly ONE send site in this library** —
`end_point_blank.commands._http.post()`. All four command classes
(access-token generation, endpoint-update, endpoint-authorize,
basic-authenticate) funnel through it. Fixing it once fixes every caller.

Grepped the full `src/` tree for `requests.`/`timeout=` to confirm no other
network call exists outside this module (only unrelated hits: a
`config.requests_url` property/string key in `configuration.py` and
`writers/*.py`, and `queue.Queue.get(timeout=1)` in `delayed_writer.py`,
which is a local queue timeout, not HTTP, and was left untouched per
instructions since the bounded queue is already fine).

## Change

`src/end_point_blank/commands/_http.py`:

```python
_CONNECT_TIMEOUT = 3  # seconds — TCP/TLS handshake budget per attempt
_READ_TIMEOUT = 5     # seconds — time to first response byte per attempt
# 3 retries × (3+5)s + 2 × 200ms ≈ 24.4s worst case, within 30s client timeouts
```

and in `post()`:

```python
timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
```

replacing the old scalar `timeout=_TIMEOUT`. The worst-case retry budget
comment was recomputed and still fits under the 30s client-side ceiling
noted originally (3 attempts × 8s effective ceiling per attempt + 2×200ms
backoff ≈ 24.4s, unchanged since connect+read sums to the same 8s ceiling
per attempt).

## Timeout-exception handling

`requests.exceptions.Timeout`, `ConnectTimeout`, and `ReadTimeout` are all
subclasses of `requests.exceptions.RequestException` (verified directly:
`issubclass(ConnectTimeout, RequestException)`, `issubclass(ReadTimeout,
RequestException)`, `issubclass(Timeout, RequestException)` → all `True`).//
The existing `post()` retry loop already catches `requests.RequestException`
broadly, logs the failure (`logger.error(...)`), backs off 200ms between
attempts, retries up to 3 times total, and returns `None` on exhaustion —
**no exception is re-raised to the caller**, satisfying the fire-and-forget
contract. No new except clause was needed; the split constants alone close
the gap, and the retry/log/swallow behavior was verified by test to hold for
`ConnectTimeout`, `ReadTimeout`, and the bare `Timeout` base class.

## Tests added

New file: `tests/commands/test_http.py`

- `test_timeout_constants_define_a_connect_read_split` — asserts
  `_CONNECT_TIMEOUT == 3` and `_READ_TIMEOUT == 5` (fails before the fix:
  `AttributeError`, since only `_TIMEOUT` existed).
- `test_post_passes_connect_read_tuple_as_timeout` — mocks the session,
  calls `_http.post(...)`, and asserts `requests.Session.post` was called
  with `timeout=(3, 5)` (fails before the fix, since the old code passed a
  bare `8`).
- `test_post_returns_none_and_logs_on_connect_timeout` — mock session
  raises `requests.exceptions.ConnectTimeout` on every call; asserts
  `post()` returns `None` and retried all 3 attempts without raising.
- `test_post_returns_none_and_logs_on_read_timeout` — same, for
  `ReadTimeout`.
- `test_post_returns_none_on_generic_timeout_without_raising` — same, for
  the base `requests.exceptions.Timeout`.

TDD sequence followed: wrote the test file first, ran it against the
unmodified `_http.py` (2 of 5 failed — the two timeout-value assertions;
the three exception-handling tests already passed against the *old* code,
confirming the existing catch-all was already correct and only the timeout
value needed to change), then applied the two-line source fix, re-ran —
all 5 passed.

## Test evidence

```
$ ./test.sh tests/commands/test_http.py -q   # before fix
FF...
2 failed, 3 passed in 0.15s
(failures: AttributeError: module has no attribute '_CONNECT_TIMEOUT', x2)

$ ./test.sh tests/commands/test_http.py -q   # after fix
.....
5 passed

$ ./test.sh -q                               # full suite, after fix
........................................................................ [ 80%]
..................                                                       [100%]
90 passed in 4.35s
```

## Queue note

`src/end_point_blank/writers/delayed_writer.py`'s `queue.Queue(maxsize=500)`
with logged-warning drop-on-full was reviewed and left untouched — it is
already correctly bounded per the task brief.

## Concerns / follow-ups

- The per-attempt worst-case budget (connect 3s + read 5s = 8s) × 3 attempts
  + 2×200ms backoff ≈ 24.4s is unchanged from before (the old scalar was
  also 8s), so no caller-visible latency ceiling changed — this is a purely
  qualitative improvement (fast-fail on dead TCP endpoints in 3s instead of
  waiting the full 8s before even establishing a connection).
- Only one send site exists today, which made this a low-risk, single-file
  change. If a future connector adds a second direct `requests.*` call
  outside `_http.py`, it should route through `_http.post()` (or get the
  same `(3, 5)` split) rather than reintroducing a bare timeout.
- No `requests.get(...)` calls exist anywhere in this library (unlike the
  brief's generic mention of `requests.get`) — everything is POST-based, so
  there was nothing further to update there.
