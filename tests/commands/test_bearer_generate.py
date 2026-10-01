import base64
import warnings

import pytest
from end_point_blank.commands.bearer_generate import BearerGenerate
from end_point_blank.configuration import Configuration


@pytest.fixture(autouse=True)
def configure():
    config = Configuration()
    config.client_id = "test-id"
    config.client_secret = "test-secret"
    yield
    config._init_defaults()


def test_generate_returns_base64_encoded_credentials():
    with pytest.deprecated_call():
        generated = BearerGenerate.generate()
    decoded = base64.b64decode(generated).decode()
    assert decoded == "test-id:test-secret"


def test_auth_header_starts_with_basic():
    with pytest.deprecated_call():
        assert BearerGenerate.auth_header().startswith("Basic ")


def test_auth_header_contains_encoded_credentials():
    with pytest.deprecated_call():
        header = BearerGenerate.auth_header()
    encoded = header[len("Basic "):]
    decoded = base64.b64decode(encoded).decode()
    assert decoded == "test-id:test-secret"


@pytest.mark.parametrize("method", ["generate", "auth_header"])
def test_each_call_warns_once_pointing_at_the_caller(method):
    # sc-1469: the header carries this service's own secret.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        getattr(BearerGenerate, method)()

    [warning] = caught
    assert warning.category is DeprecationWarning
    assert f"BearerGenerate.{method} is deprecated" in str(warning.message)
    assert "Authorization.header(base_url)" in str(warning.message)
    assert "test-secret" not in str(warning.message)
    assert warning.filename == __file__
