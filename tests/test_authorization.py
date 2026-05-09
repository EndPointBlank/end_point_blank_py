import base64
import pytest
from end_point_blank.configuration import Configuration
from end_point_blank.authorization import Authorization


@pytest.fixture(autouse=True)
def configure():
    config = Configuration()
    config.client_id = "test-client-id"
    config.client_secret = "test-client-secret"
    yield
    config._init_defaults()


def test_basic_credentials_encodes_correctly():
    creds = Authorization.basic_credentials()
    decoded = base64.b64decode(creds).decode()
    assert decoded == "test-client-id:test-client-secret"


def test_header_with_no_hostname_returns_basic():
    header = Authorization.header()
    assert header.startswith("Basic ")
    encoded = header[len("Basic "):]
    decoded = base64.b64decode(encoded).decode()
    assert decoded == "test-client-id:test-client-secret"


def test_header_with_none_hostname_returns_basic():
    header = Authorization.header(hostname=None)
    assert header.startswith("Basic ")
