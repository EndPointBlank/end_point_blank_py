"""
``epb.configure`` is the library's entire public setup surface. Its one rule is
that omitted arguments leave the existing value alone — configure() is commonly
called more than once (a base call at import, a narrower one per environment),
and a default overwriting a previously set value is silent misconfiguration.
"""

import pytest

import end_point_blank as epb
from end_point_blank.configuration import Configuration, LogMode

SETTINGS = [
    ("client_id", "cid-1"),
    ("client_secret", "secret-1"),
    ("base_url", "https://intake.test"),
    ("environment", "staging"),
    ("app_name", "students-api"),
    ("worker_count", 9),
    ("log_mode", LogMode.DELAYED),
    ("application_version", "2026.07.31"),
    ("token_ttl", 900),
    ("cache_ttl", 60),
    ("masking_rules", [{"target": "request_body", "path": "$.password"}]),
]


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for key in ("ENDPOINTBLANK_CLIENT_ID", "ENDPOINTBLANK_CLIENT_SECRET", "ENDPOINTBLANK_BASE_URL",
                "ENDPOINTBLANK_APP_NAME", "ENDPOINTBLANK_ENV"):
        monkeypatch.delenv(key, raising=False)
    config = Configuration()
    config._init_defaults()
    yield config
    config._init_defaults()


class TestSettingValues:
    @pytest.mark.parametrize("name, value", SETTINGS, ids=[name for name, _ in SETTINGS])
    def test_each_setting_reaches_the_configuration(self, name, value, _reset):
        epb.configure(**{name: value})

        assert getattr(_reset, name) == value

    def test_a_custom_version_finder_is_installed(self, _reset):
        def finder(environ):
            return "7"

        epb.configure(version_finder=finder)

        assert _reset.version_finder is finder

    def test_a_mask_hook_is_installed(self, _reset):
        def hook(payload, record_type):
            return payload

        epb.configure(mask_hook=hook)

        assert _reset.mask_hook is hook

    def test_several_settings_can_be_given_at_once(self, _reset):
        epb.configure(client_id="cid", client_secret="secret", app_name="students-api")

        assert (_reset.client_id, _reset.client_secret, _reset.app_name) == ("cid", "secret", "students-api")


class TestOmittedArguments:
    def test_omitted_settings_are_left_alone(self, _reset):
        epb.configure(client_id="cid", app_name="students-api")

        epb.configure(app_name="renamed")

        assert _reset.client_id == "cid"

    def test_configuring_nothing_changes_nothing(self, _reset):
        epb.configure(client_id="cid", worker_count=9)

        epb.configure()

        assert _reset.client_id == "cid"
        assert _reset.worker_count == 9

    def test_a_later_call_overrides_an_earlier_one(self, _reset):
        epb.configure(app_name="first")
        epb.configure(app_name="second")

        assert _reset.app_name == "second"


class TestThePublicSurface:
    def test_the_names_the_readme_documents_are_importable(self):
        assert {"configure", "Configuration", "LogMode", "UnauthorizedError", "VERSION"} <= set(epb.__all__)

    def test_configuration_is_shared_with_the_library_internals(self):
        # configure() writes to the singleton every command reads from; a second
        # instance would mean settings applied at boot are invisible at runtime.
        epb.configure(app_name="students-api")

        assert Configuration().app_name == "students-api"
