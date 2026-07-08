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
    config = Configuration()
    assert config.base_url == "https://in.endpointblank.com"
    assert config.log_base_url == "https://log.endpointblank.com"


def test_default_worker_count():
    assert Configuration().worker_count == 4


def test_default_log_mode():
    assert Configuration().log_mode == LogMode.DIRECT


def test_default_cache_ttl():
    assert Configuration().cache_ttl == 300


def test_url_properties():
    config = Configuration()
    config.base_url = "https://control.example.com"
    config.log_base_url = "https://log.example.com"
    # log_base_url-derived URLs
    assert config.log_url == "https://log.example.com/api/application_logs"
    assert config.application_errors_url == "https://log.example.com/api/application_errors"
    assert config.requests_url == "https://log.example.com/api/application_requests"
    assert config.responses_url == "https://log.example.com/api/application_responses"
    # base_url-derived URLs
    assert config.access_token_url == "https://control.example.com/api/access_token"
    assert config.authorize_url == "https://control.example.com/api/authorize"
    assert config.endpoint_update_url == "https://control.example.com/api/application_updates"
    assert config.endpoint_error_url == "https://control.example.com/api/endpoint_errors"


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


def test_masking_config_defaults_and_set():
    from end_point_blank.configuration import Configuration
    c = Configuration()
    c._init_defaults()
    assert c.masking_rules == []
    assert c.mask_hook is None
    c.masking_rules = [{"target": "request_body", "path": "$.a", "replacement_value": "..."}]
    assert c.masking_rules[0]["target"] == "request_body"


# ENDPOINTBLANK_* environment variable fallback configuration.
#
# Precedence for every mapped setting: explicit value set via the configure
# API > os.environ["ENDPOINTBLANK_*"] > built-in default.
ENV_VAR_SETTINGS = {
    "client_id": ("ENDPOINTBLANK_CLIENT_ID", None),
    "client_secret": ("ENDPOINTBLANK_CLIENT_SECRET", None),
    "base_url": ("ENDPOINTBLANK_BASE_URL", "https://in.endpointblank.com"),
    "log_base_url": ("ENDPOINTBLANK_LOG_BASE_URL", "https://log.endpointblank.com"),
    "app_name": ("ENDPOINTBLANK_APP_NAME", None),
    "environment": ("ENDPOINTBLANK_ENV", None),
}


@pytest.mark.parametrize("attr,env_pair", ENV_VAR_SETTINGS.items(), ids=ENV_VAR_SETTINGS.keys())
def test_env_var_used_when_no_explicit_value(monkeypatch, attr, env_pair):
    env_var, _default = env_pair
    monkeypatch.setenv(env_var, "from-env")
    config = Configuration()
    assert getattr(config, attr) == "from-env"


@pytest.mark.parametrize("attr,env_pair", ENV_VAR_SETTINGS.items(), ids=ENV_VAR_SETTINGS.keys())
def test_explicit_value_beats_env_var(monkeypatch, attr, env_pair):
    env_var, _default = env_pair
    monkeypatch.setenv(env_var, "from-env")
    config = Configuration()
    setattr(config, attr, "from-explicit")
    assert getattr(config, attr) == "from-explicit"


@pytest.mark.parametrize("attr,env_pair", ENV_VAR_SETTINGS.items(), ids=ENV_VAR_SETTINGS.keys())
def test_env_var_beats_default(monkeypatch, attr, env_pair):
    env_var, default = env_pair
    monkeypatch.setenv(env_var, "from-env")
    config = Configuration()
    assert getattr(config, attr) == "from-env"
    assert getattr(config, attr) != default


@pytest.mark.parametrize("attr,env_pair", ENV_VAR_SETTINGS.items(), ids=ENV_VAR_SETTINGS.keys())
def test_default_used_when_no_explicit_value_and_no_env_var(monkeypatch, attr, env_pair):
    env_var, default = env_pair
    monkeypatch.delenv(env_var, raising=False)
    config = Configuration()
    assert getattr(config, attr) == default
