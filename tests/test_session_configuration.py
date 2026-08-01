"""
``SessionConfiguration.env_name`` decides which environment every audit row is
attributed to. Getting it wrong does not fail loudly — it silently files
production traffic under the wrong environment in the portal.
"""

import pytest

from end_point_blank.configuration import Configuration
from end_point_blank.request_store import RequestStore
from end_point_blank.session_configuration import SessionConfiguration

FALLBACK_VARS = ("FLASK_ENV", "APP_ENV", "ENVIRONMENT", "DJANGO_ENV")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for key in (*FALLBACK_VARS, "ENDPOINTBLANK_ENV"):
        monkeypatch.delenv(key, raising=False)
    config = Configuration()
    config._init_defaults()
    RequestStore.clear()
    yield config
    RequestStore.clear()
    config._init_defaults()


class TestTheEnvironmentName:
    def test_an_explicit_configuration_wins(self, _reset, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "development")
        _reset.environment = "staging"

        assert SessionConfiguration.env_name() == "staging"

    @pytest.mark.parametrize("variable", FALLBACK_VARS)
    def test_falls_back_to_each_supported_environment_variable(self, variable, monkeypatch):
        monkeypatch.setenv(variable, "development")

        assert SessionConfiguration.env_name() == "development"

    def test_prefers_flask_env_over_the_later_variables(self, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "development")
        monkeypatch.setenv("APP_ENV", "staging")

        assert SessionConfiguration.env_name() == "development"

    def test_defaults_to_production_when_nothing_is_set(self):
        # Defaulting to production is the safe direction: real traffic filed as
        # production is correct, whereas production filed as development would
        # hide it from the environment anyone is actually watching.
        assert SessionConfiguration.env_name() == "production"

    def test_ignores_a_blank_environment_variable(self, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "")
        monkeypatch.setenv("APP_ENV", "staging")

        assert SessionConfiguration.env_name() == "staging"


class TestTheRequestEnviron:
    def test_returns_the_environ_of_the_request_in_flight(self):
        environ = {"PATH_INFO": "/students"}
        RequestStore.set(environ)

        assert SessionConfiguration.environ() is environ

    def test_is_none_outside_a_request(self):
        assert SessionConfiguration.environ() is None
