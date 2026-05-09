import base64
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
    generated = BearerGenerate.generate()
    decoded = base64.b64decode(generated).decode()
    assert decoded == "test-id:test-secret"


def test_auth_header_starts_with_basic():
    assert BearerGenerate.auth_header().startswith("Basic ")


def test_auth_header_contains_encoded_credentials():
    header = BearerGenerate.auth_header()
    encoded = header[len("Basic "):]
    decoded = base64.b64decode(encoded).decode()
    assert decoded == "test-id:test-secret"
