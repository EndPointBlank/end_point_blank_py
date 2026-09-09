class UnauthorizedError(Exception):
    """
    Raised when a request fails authentication or authorization.

    This exception is intentionally not logged by the middleware,
    as unauthorized access attempts are expected to occur.

    ``status_code`` carries the status intake answered the authenticate or
    authorize call with, so a handler can tell the two refusals apart: 401
    means the credential was not accepted and the integrator should check it,
    403 means the credential was fine but no grant covers this endpoint and the
    integrator should ask for one. Collapsing both to 401 sends them to debug
    the wrong thing. The other SDKs each carry the same value under their own
    idiomatic name (``statusCode`` in JS, ``getStatusCode()`` in Java,
    ``status`` in Ruby); this is Python's.

    It defaults to 401 so that the existing single-argument construction keeps
    working unchanged, and because 401 is the safe reading of a refusal that
    arrives with no status attached. The decorators never lean on that default:
    they pass intake's status, or 503 when intake did not answer at all.
    """

    def __init__(self, message: str = "", status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code

    def __reduce__(self):
        # Exception's default __reduce__ rebuilds from self.args alone, which
        # here is the message only -- an unpickled refusal would silently come
        # back as a 401 no matter what intake said. That is the exact failure
        # this attribute exists to remove, so it must survive the round trip
        # rather than quietly reverting to the default.
        return (self.__class__, (self.args[0] if self.args else "", self.status_code))


def refusal_from(response, action: str) -> UnauthorizedError:
    """
    Build the :class:`UnauthorizedError` for a non-201 answer to intake's
    authenticate or authorize call. ``action`` is ``"Authentication"`` or
    ``"Authorization"``.

    One function rather than a copy per decorator. The four call sites -- Flask
    and Django x authenticate and authorize -- were four transcriptions of one
    decision, and four copies is how one of them acquires a fix the other three
    do not. That is not hypothetical here: it is how the status came to be
    dropped at every one of them at once, and how it would come to be restored
    at only some of them.
    """
    if response is None:
        # Intake never answered at all, so nothing refused this caller and 401
        # would blame a credential that was never judged. 503 says the true
        # thing -- the check could not be made -- and is what the other four
        # SDKs already send for this same case: JS `response ? response.status
        # : 503`, Java the same ternary, Ruby `result&.status || 503`, and
        # Elixir a literal `send_resp(503, ...)` on its `{:error, reason}`
        # branch.
        return UnauthorizedError(
            f"{action} failed: {action} service unavailable", 503
        )

    try:
        reason = response.json().get("error", response.text)
    except Exception:
        reason = response.text

    # Intake's verdict verbatim. 401 tells an integrator to check the
    # credential, 403 tells them to ask for a grant; collapsing the two sends
    # them to debug the wrong thing.
    return UnauthorizedError(f"{action} failed: {reason}", response.status_code)
