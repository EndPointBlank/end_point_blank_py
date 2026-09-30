"""
The message of a TokenUnavailableError is what reaches logs and error
reporting, and the caller controls the URL in it, so it must never repeat the
URL's userinfo, query or fragment (sc-1469 review on js#54), and neither may the error's own attributes.
"""

from end_point_blank.token_unavailable_error import TokenUnavailableError

RAW = "https://user:hunter2@api.provider.test:8443/v1/things?api_key=s3cret#frag"


def test_the_message_names_only_scheme_host_and_path():
    error = TokenUnavailableError(RAW)

    assert "for https://api.provider.test:8443/v1/things: " in str(error)
    for secret in ("user", "hunter2", "api_key", "s3cret", "frag"):
        assert secret not in str(error)


def test_the_error_keeps_only_the_stripped_url():
    # Error reporters capture attributes as well as the message (sc-1469
    # review), so the raw URL is kept nowhere on the error.
    assert TokenUnavailableError(RAW).base_url == "https://api.provider.test:8443/v1/things"


def test_an_unparseable_url_is_left_out_of_the_message():
    error = TokenUnavailableError("not a url ?token=s3cret")

    assert "s3cret" not in str(error)
    assert "could not be parsed" in str(error)
    assert error.base_url is None
