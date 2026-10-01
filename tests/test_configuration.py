import pytest
import end_point_blank as epb
from end_point_blank.configuration import Configuration, LogMode, client_id_slug


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


# sc-970: assigning the attribute directly is held to the same rule as
# configure(cache_ttl=...). Before, Configuration().cache_ttl = None was stored
# as-is and surfaced only as a TypeError on the next cache read.
@pytest.mark.parametrize("value", [0, 1, 300, 86400])
def test_cache_ttl_accepts_zero_and_positive_ints(value):
    config = Configuration()
    config.cache_ttl = value
    assert config.cache_ttl == value


@pytest.mark.parametrize(
    "value",
    [None, -1, "abc", 3.5, True, False],
    ids=["None", "negative", "str", "float", "True", "False"],
)
def test_assigning_an_invalid_cache_ttl_raises_at_assignment(value):
    config = Configuration()
    config.cache_ttl = 120
    with pytest.raises(ValueError) as exc_info:
        config.cache_ttl = value
    assert "cache_ttl" in str(exc_info.value)
    assert config.cache_ttl == 120


@pytest.mark.parametrize("attr", ["base_url", "log_base_url"])
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://control.example.com/", "https://control.example.com"),
        ("https://control.example.com//", "https://control.example.com"),
        ("https://control.example.com///", "https://control.example.com"),
    ],
    ids=["one-slash", "two-slashes", "three-slashes"],
)
def test_trailing_slash_is_stripped(attr, raw, expected):
    config = Configuration()
    setattr(config, attr, raw)
    assert getattr(config, attr) == expected


def test_trailing_slash_stripped_base_url_builds_correct_urls():
    config = Configuration()
    config.base_url = "https://control.example.com/"
    config.log_base_url = "https://log.example.com/"
    assert config.authorize_url == "https://control.example.com/api/authorize"
    assert config.access_token_url == "https://control.example.com/api/access_token"
    assert config.log_url == "https://log.example.com/api/application_logs"


@pytest.mark.parametrize("attr", ["base_url", "log_base_url"])
@pytest.mark.parametrize(
    "raw",
    [
        "https://control.example.com/api",
        "https://control.example.com/api/",
        "https://control.example.com/api//",
    ],
    ids=["bare", "trailing-slash", "trailing-slashes"],
)
def test_api_suffixed_base_url_raises(attr, raw):
    config = Configuration()
    setattr(config, attr, raw)
    with pytest.raises(ValueError) as exc_info:
        getattr(config, attr)
    message = str(exc_info.value)
    assert attr in message
    assert "/api" in message
    assert raw in message or raw.rstrip("/") in message


def test_api_suffixed_base_url_and_log_base_url_raise_independently():
    config = Configuration()
    config.base_url = "https://control.example.com/api"
    config.log_base_url = "https://log.example.com"
    # log_base_url is clean, so reading it must not raise even though
    # base_url (a completely separate setting) is misconfigured.
    assert config.log_base_url == "https://log.example.com"
    with pytest.raises(ValueError):
        config.base_url


def test_clean_base_url_is_unaffected():
    config = Configuration()
    config.base_url = "https://control.example.com"
    config.log_base_url = "https://log.example.com"
    assert config.base_url == "https://control.example.com"
    assert config.log_base_url == "https://log.example.com"
    assert config.authorize_url == "https://control.example.com/api/authorize"
    assert config.log_url == "https://log.example.com/api/application_logs"


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


# sc-1463. *.in.endpointblank.com has no DNS or TLS in production yet, so
# derivation is off by default, and off must mean exactly today's answer.
class TestBaseUrlDerivedFromClientId:
    PREFIXED = "acima-x7k2mq.ijXI+MVwmrC5xH/9ZuGiQlAbAyobTqMa"
    DEFAULT = "https://in.endpointblank.com"
    DERIVED = "https://acima-x7k2mq.in.endpointblank.com"

    @pytest.fixture(autouse=True)
    def _no_env(self, monkeypatch):
        for key in ("ENDPOINTBLANK_CLIENT_ID", "ENDPOINTBLANK_BASE_URL", "ENDPOINTBLANK_LOG_BASE_URL"):
            monkeypatch.delenv(key, raising=False)

    def test_is_off_by_default(self):
        assert Configuration().derive_base_url_from_client_id is False

    @pytest.mark.parametrize("client_id", [PREFIXED, "plain-client-id", "my.client", None])
    def test_with_derivation_off_every_client_id_resolves_to_todays_default(self, client_id):
        config = Configuration()
        config.client_id = client_id

        assert config.base_url == self.DEFAULT
        assert config.authorize_url == self.DEFAULT + "/api/authorize"
        assert config.access_token_url == self.DEFAULT + "/api/access_token"

    def test_with_derivation_off_a_prefixed_env_client_id_changes_nothing(self, monkeypatch):
        monkeypatch.setenv("ENDPOINTBLANK_CLIENT_ID", self.PREFIXED)
        assert Configuration().base_url == self.DEFAULT

    def test_with_derivation_on_a_slug_prefixed_client_id_calls_its_organizations_intake(self):
        epb.configure(derive_base_url_from_client_id=True, client_id=self.PREFIXED)
        config = Configuration()

        assert config.base_url == self.DERIVED
        assert config.authorize_url == self.DERIVED + "/api/authorize"
        assert config.access_token_url == self.DERIVED + "/api/access_token"
        assert config.endpoint_update_url == self.DERIVED + "/api/application_updates"

    def test_with_derivation_on_the_client_id_may_come_from_the_env(self, monkeypatch):
        monkeypatch.setenv("ENDPOINTBLANK_CLIENT_ID", self.PREFIXED)
        epb.configure(derive_base_url_from_client_id=True)

        assert Configuration().base_url == self.DERIVED

    def test_with_derivation_on_an_explicit_base_url_still_wins(self):
        epb.configure(
            derive_base_url_from_client_id=True,
            client_id=self.PREFIXED,
            base_url="https://explicit.example",
        )

        assert Configuration().base_url == "https://explicit.example"

    def test_with_derivation_on_the_env_base_url_still_wins(self, monkeypatch):
        monkeypatch.setenv("ENDPOINTBLANK_BASE_URL", "https://env.example")
        epb.configure(derive_base_url_from_client_id=True, client_id=self.PREFIXED)

        assert Configuration().base_url == "https://env.example"

    # "my.client" has a dot but no slug before it: the portal has always
    # accepted a typed client_id, so a legacy id like this can exist.
    @pytest.mark.parametrize(
        "client_id",
        [
            "plain-client-id",
            "ijXI+MVwmrC5xH/9ZuGiQlAbAyobTqMa",
            "my.client",
            "acima-x7k2mq.",
            ".acima-x7k2mq",
            "Acima-x7k2mq.abc",
            "acima-x7k2m.abc",
            "acima-x7k2mqq.abc",
            "-acima-x7k2mq.abc",
            "acima--x7k2mq.abc",
        ],
    )
    def test_with_derivation_on_a_client_id_without_a_slug_prefix_calls_the_default(self, client_id):
        epb.configure(derive_base_url_from_client_id=True, client_id=client_id)
        assert Configuration().base_url == self.DEFAULT

    def test_with_derivation_on_and_no_client_id_calls_the_default(self):
        epb.configure(derive_base_url_from_client_id=True)
        assert Configuration().base_url == self.DEFAULT

    def test_never_derives_the_logs_hostname(self):
        epb.configure(derive_base_url_from_client_id=True, client_id=self.PREFIXED)
        config = Configuration()

        assert config.log_base_url == "https://log.endpointblank.com"
        assert config.log_url == "https://log.endpointblank.com/api/application_logs"

    def test_derivation_can_be_turned_off_again(self):
        epb.configure(derive_base_url_from_client_id=True, client_id=self.PREFIXED)
        epb.configure(derive_base_url_from_client_id=False)

        assert Configuration().base_url == self.DEFAULT

    @pytest.mark.parametrize("value", ["true", "false", 1, 0, "yes"])
    def test_configure_refuses_a_value_that_is_not_a_bool_applying_nothing(self, value):
        with pytest.raises(ValueError, match="derive_base_url_from_client_id"):
            epb.configure(client_id=self.PREFIXED, derive_base_url_from_client_id=value)

        assert Configuration().client_id is None
        assert Configuration().derive_base_url_from_client_id is False

    @pytest.mark.parametrize("value", ["true", 1, None])
    def test_assigning_a_value_that_is_not_a_bool_raises(self, value):
        config = Configuration()
        with pytest.raises(ValueError, match="derive_base_url_from_client_id"):
            config.derive_base_url_from_client_id = value
        assert config.derive_base_url_from_client_id is False


class TestClientIdSlug:
    def test_answers_the_slug_of_a_prefixed_client_id(self):
        assert client_id_slug("acima-x7k2mq.ijXI+MVwmrC5xH/9ZuGiQlAbAyobTqMa") == "acima-x7k2mq"
        # The longest label app_portal makes: 20 characters, then the random part.
        assert client_id_slug("abcdefghij0123456789-x7k2mq.r") == "abcdefghij0123456789-x7k2mq"
        # The fallback label for an organization with no usable name.
        assert client_id_slug("org-x7k2mq.r") == "org-x7k2mq"

    def test_splits_on_the_first_dot_only(self):
        assert client_id_slug("acima-x7k2mq.a.b") == "acima-x7k2mq"

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "no-dot",
            "my.client",
            "acima-x7k2mq.",
            "abcdefghij01234567890-x7k2mq.r",
            "acima-x7k2mq-.r",
            "acima_x7k2mq.r",
            # Nothing outside [a-z0-9-] may reach the derived hostname.
            "acima-x7k2mq\n.r",
            "evil.com@acima-x7k2mq.r",
            "a:1-x7k2mq.r",
            "ACIMA-X7K2MQ.r",
            # Non-ASCII digits and letters that \w or \d would admit.
            "acima-x7k2m٣.r",
            "ácima-x7k2mq.r",
            123,
        ],
    )
    def test_answers_none_for_anything_else(self, value):
        assert client_id_slug(value) is None
