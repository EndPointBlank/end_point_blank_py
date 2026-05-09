import pytest
from end_point_blank.commands.version_finder import VersionFinder
from end_point_blank.configuration import Configuration


@pytest.fixture(autouse=True)
def reset_config():
    Configuration().version_finder = None
    yield
    Configuration().version_finder = None


def make_environ(**kwargs):
    base = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "QUERY_STRING": "",
    }
    base.update(kwargs)
    return base


def test_finds_version_from_accept_header():
    environ = make_environ(HTTP_ACCEPT="application/vnd.api.v2+json")
    assert VersionFinder().find(environ) == "2"


def test_finds_version_from_x_api_version_header():
    environ = make_environ(HTTP_X_API_VERSION="v3")
    assert VersionFinder().find(environ) == "3"


def test_finds_version_from_content_type_header():
    environ = make_environ(CONTENT_TYPE="application/vnd.myapp.v1+json")
    assert VersionFinder().find(environ) == "1"


def test_finds_version_from_query_param():
    environ = make_environ(QUERY_STRING="version=v5")
    assert VersionFinder().find(environ) == "5"


def test_finds_version_from_url_path():
    environ = make_environ(PATH_INFO="/api/v4/users")
    assert VersionFinder().find(environ) == "4"


def test_returns_none_when_no_version():
    environ = make_environ(HTTP_ACCEPT="application/json", PATH_INFO="/users")
    assert VersionFinder().find(environ) is None


def test_accept_header_takes_precedence_over_path():
    environ = make_environ(
        HTTP_ACCEPT="application/vnd.api.v2+json",
        PATH_INFO="/api/v1/users",
    )
    assert VersionFinder().find(environ) == "2"


def test_custom_version_finder_takes_precedence():
    Configuration().version_finder = lambda env: "42"
    environ = make_environ(PATH_INFO="/api/v1/users")
    assert VersionFinder().find(environ) == "42"


def test_query_param_version_without_v_prefix():
    environ = make_environ(QUERY_STRING="version=v7&other=true")
    assert VersionFinder().find(environ) == "7"
