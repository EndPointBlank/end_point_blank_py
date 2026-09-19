"""
``epb.configure`` is the library's entire public setup surface. Its one rule is
that omitted arguments leave the existing value alone — configure() is commonly
called more than once (a base call at import, a narrower one per environment),
and a default overwriting a previously set value is silent misconfiguration.
"""

import importlib

import pytest

import end_point_blank as epb
from end_point_blank.commands.authentication_cache import AuthenticationCache
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


class TestCacheTtl:
    """
    sc-970: one ``cache_ttl`` rule, the same in the JS, Java, Elixir, Python and
    Rails SDKs.

    - omitted: the default, 300 seconds (or whatever an earlier call set)
    - ``0``: the authorization cache is disabled
    - ``None``, a negative number, or anything that is not an ``int``: a
      ``ValueError`` from ``configure()`` itself

    Before this, ``cache_ttl=None`` was indistinguishable from omitting the
    argument, a negative number quietly disabled the cache, a float was used
    as-is, and a string was stored and only blew up as a ``TypeError`` on the
    first cache read.
    """

    @pytest.fixture(autouse=True)
    def _empty_cache(self):
        AuthenticationCache().clear()
        yield
        AuthenticationCache().clear()

    def test_omitted_means_the_default_of_300_seconds(self, _reset):
        epb.configure(client_id="cid")

        assert _reset.cache_ttl == 300

    def test_omitted_leaves_an_earlier_value_alone(self, _reset):
        epb.configure(cache_ttl=60)

        epb.configure(client_id="cid")
        epb.configure()

        assert _reset.cache_ttl == 60

    def test_zero_is_accepted_and_disables_the_cache(self, _reset):
        epb.configure(cache_ttl=0)

        cache = AuthenticationCache()
        cache.store("key", {"granted": True})

        assert _reset.cache_ttl == 0
        # Checked before any read: an enabled cache with a 0-second TTL would
        # store the entry and let it expire, so a read alone cannot tell
        # "disabled" from "expired".
        assert cache.size() == 0
        assert cache.retrieve("key") is None

    def test_a_positive_value_is_used_by_the_cache(self, _reset):
        epb.configure(cache_ttl=60)

        cache = AuthenticationCache()
        cache.store("key", {"granted": True})

        assert cache.retrieve("key") == {"granted": True}

    def test_an_explicit_none_raises_and_says_to_omit_it(self, _reset):
        epb.configure(cache_ttl=60)

        with pytest.raises(ValueError) as exc_info:
            epb.configure(cache_ttl=None)

        message = str(exc_info.value)
        assert "cache_ttl" in message
        assert "omit" in message.lower()
        assert "300" in message
        assert _reset.cache_ttl == 60

    @pytest.mark.parametrize("value", [-1, -5, -300])
    def test_a_negative_value_raises(self, value, _reset):
        epb.configure(cache_ttl=60)

        with pytest.raises(ValueError) as exc_info:
            epb.configure(cache_ttl=value)

        assert "cache_ttl" in str(exc_info.value)
        assert _reset.cache_ttl == 60

    @pytest.mark.parametrize(
        "value",
        ["abc", "60", 3.5, 300.0, True, False, [60]],
        ids=["str", "numeric-str", "float", "whole-float", "True", "False", "list"],
    )
    def test_a_value_that_is_not_an_int_raises(self, value, _reset):
        epb.configure(cache_ttl=60)

        with pytest.raises(ValueError) as exc_info:
            epb.configure(cache_ttl=value)

        assert "cache_ttl" in str(exc_info.value)
        assert _reset.cache_ttl == 60

    def test_a_rejected_cache_ttl_applies_nothing_else_from_the_same_call(self, _reset):
        # The error is raised before any setting is written, so a caller that
        # catches it is not left running on half of the call it made.
        epb.configure(client_id="before", app_name="before")

        with pytest.raises(ValueError):
            epb.configure(client_id="after", app_name="after", cache_ttl=-1)

        assert (_reset.client_id, _reset.app_name) == ("before", "before")

    def test_omitted_still_means_omitted_after_the_package_is_reloaded(self, _reset):
        # A ``configure`` imported before an ``importlib.reload`` of the
        # package (e.g. ``from end_point_blank import configure`` in a notebook
        # or REPL, then a reload of the package) keeps its original default for
        # ``cache_ttl``. That default must still be the sentinel the reloaded
        # module compares against, or the omitted argument is validated as a
        # value and rejected.
        configure_from_before_the_reload = epb.configure
        importlib.reload(epb)
        epb.configure(cache_ttl=60)

        configure_from_before_the_reload(client_id="cid")

        assert (_reset.client_id, _reset.cache_ttl) == ("cid", 60)


class TestConfigureIsAllOrNothing:
    """
    sc-1266: across all five SDKs, ``configure`` must be all-or-nothing. The
    sc-970 reviews found that Rails and Java kept a partial update when one of
    several supplied fields failed validation — the caller got an error, but
    the config was left half-updated. sc-1266 requires every SDK to be
    checked and to carry this same regression test regardless of which case
    applies.

    This SDK was already all-or-nothing before this story: ``cache_ttl`` is
    the only validated field, and ``configure()`` (``src/end_point_blank/
    __init__.py``) validates it via ``_validate_cache_ttl`` *before* the
    sequential ``if x is not None: config.x = x`` assignment block runs, so a
    rejected call never reaches an assignment. ``TestCacheTtl`` above already
    has a test covering the same property (added under sc-970); this class
    pins it again under the sc-1266 name, in the exact shape the story's
    acceptance criteria describe, so the two stories don't share one test
    that could be edited for one reason and silently stop covering the
    other.
    """

    def test_a_rejected_call_applies_none_of_its_other_fields(self, _reset):
        epb.configure(client_id="before-id")

        with pytest.raises(ValueError):
            epb.configure(client_id="new-id", cache_ttl=-1)

        assert _reset.client_id == "before-id"


class TestThePublicSurface:
    def test_the_names_the_readme_documents_are_importable(self):
        assert {"configure", "Configuration", "LogMode", "UnauthorizedError", "VERSION"} <= set(epb.__all__)

    def test_configuration_is_shared_with_the_library_internals(self):
        # configure() writes to the singleton every command reads from; a second
        # instance would mean settings applied at boot are invisible at runtime.
        epb.configure(app_name="students-api")

        assert Configuration().app_name == "students-api"
