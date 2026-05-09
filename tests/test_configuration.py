import pytest
import end_point_blank as epb
from end_point_blank.configuration import Configuration, LogMode


@pytest.fixture(autouse=True)
def reset_config():
    config = Configuration()
    config._init_defaults()
    yield
    config._init_defaults()


def test_singleton_returns_same_instance():
    assert Configuration() is Configuration()


def test_default_base_url():
    assert Configuration().base_url == "https://endpointblank.com/api"


def test_default_worker_count():
    assert Configuration().worker_count == 4


def test_default_log_mode():
    assert Configuration().log_mode == LogMode.DIRECT


def test_default_cache_ttl():
    assert Configuration().cache_ttl == 300


def test_url_properties():
    config = Configuration()
    config.base_url = "https://example.com/api"
    assert config.log_url == "https://example.com/api/api/logs"
    assert config.access_token_url == "https://example.com/api/api/access_token"
    assert config.authorize_url == "https://example.com/api/api/authorize"
    assert config.endpoint_update_url == "https://example.com/api/api/application_updates"
    assert config.application_errors_url == "https://example.com/api/api/application_errors"
    assert config.endpoint_error_url == "https://example.com/api/api/endpoint_errors"


def test_configure_sets_values():
    epb.configure(
        client_id="my-id",
        client_secret="my-secret",
        app_name="test-app",
        environment="staging",
    )
    config = Configuration()
    assert config.client_id == "my-id"
    assert config.client_secret == "my-secret"
    assert config.app_name == "test-app"
    assert config.environment == "staging"


def test_configure_ignores_none_values():
    config = Configuration()
    config.client_id = "original"
    epb.configure(client_secret="new-secret")
    assert config.client_id == "original"
    assert config.client_secret == "new-secret"


def test_configure_log_mode():
    epb.configure(log_mode=LogMode.DELAYED)
    assert Configuration().log_mode == LogMode.DELAYED
