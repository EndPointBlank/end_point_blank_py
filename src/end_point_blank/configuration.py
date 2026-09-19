from __future__ import annotations

import os
from enum import Enum
from typing import Callable, Optional


class LogMode(Enum):
    DIRECT = "direct"
    DELAYED = "delayed"


# Every request/log URL in this file is built by appending this literal to a
# configured base URL, e.g. ``f"{self.base_url}/api/authorize"`` below. A base
# URL that already ends in it cannot be fixed by stripping -- appending
# ``API_SUFFIX`` again produces ``/api/api/...``, which intake 404s on -- so
# that case is rejected outright rather than silently mangled.
API_SUFFIX = "/api"


def _normalize_base_url(value: str, setting_name: str) -> str:
    """
    Normalize a configured base URL (``base_url`` or ``log_base_url``) at the
    point it is read.

    A trailing slash is the single most common way to mistype a base URL and
    is unambiguous to fix -- ``https://in.example.com/`` and
    ``https://in.example.com`` mean the same origin -- so it is stripped
    silently here rather than raising. Left unstripped it produces a doubled
    slash (``.../api`` becomes ``..//api``) that intake 404s on, and that
    404 is swallowed to a warning by the write path, so the misconfiguration
    would otherwise never surface (see ``feedback_no_silent_failures``).

    A base URL that already ends in ``API_SUFFIX`` (e.g. someone configured
    ``https://in.example.com/api``) is a different kind of mistake: there is
    no normalization that makes it correct, because every URL builder in
    this file appends ``API_SUFFIX`` again on top of it. That is a
    configuration error the SDK can and should catch loudly at configure
    time instead of failing silently on every write forever, so it raises.
    """
    stripped = value.rstrip("/")
    if stripped.endswith(API_SUFFIX):
        raise ValueError(
            f"{setting_name} is set to {value!r}, which already ends in "
            f"{API_SUFFIX!r}. {setting_name} must be the bare origin the "
            f"API is served from (e.g. 'https://in.endpointblank.com'), not "
            f"the API path itself -- this SDK builds request URLs by "
            f"appending '{API_SUFFIX}/...' to {setting_name}, so leaving "
            f"the suffix in would send requests to "
            f"'{stripped}{API_SUFFIX}/...'. Remove the trailing "
            f"{API_SUFFIX!r} from {setting_name}."
        )
    return stripped


DEFAULT_CACHE_TTL = 300  # seconds


class _Unset(Enum):
    """
    Type of ``_UNSET``: the default for ``configure()`` arguments where an
    explicit ``None`` must not be read as "omitted". Every other
    ``configure()`` argument treats ``None`` as omitted; ``cache_ttl`` cannot,
    because sc-970 makes ``cache_ttl=None`` an error.

    A one-member ``Enum`` rather than a bare ``object()`` so the argument can
    be annotated ``int | _Unset`` and a type checker narrows it to ``int``
    after ``is not _UNSET``. It lives here rather than in the package
    ``__init__`` so an ``importlib.reload`` of the package reuses this object:
    a ``configure`` imported before the reload keeps it as its default, and
    must still find it identical to the ``_UNSET`` it compares against.
    """

    UNSET = "UNSET"


_UNSET = _Unset.UNSET


def _validate_cache_ttl(value: object) -> int:
    """
    Enforce the ``cache_ttl`` rule shared by the JS, Java, Elixir, Python and
    Rails SDKs (sc-970), and return *value* unchanged if it passes:

    - an ``int`` of ``0`` or more is accepted, and ``0`` disables the
      authorization cache;
    - ``None``, a negative number, or anything that is not an ``int`` raises
      ``ValueError``.

    To get the default, omit ``cache_ttl`` from ``configure()`` -- ``None`` is
    not a spelling of "the default". A ``bool`` is refused although Python
    counts it as an ``int``: ``True`` would otherwise be a one-second TTL and
    ``False`` would switch caching off, neither of which anyone means by
    writing a flag.

    This runs where the value is set, not where the cache reads it, so a bad
    value fails at startup rather than as a ``TypeError`` on the first
    ``@authorized`` request.
    """
    if value is None:
        raise ValueError(
            "cache_ttl is None. To use the default of "
            f"{DEFAULT_CACHE_TTL} seconds, omit cache_ttl from configure() "
            "rather than passing None; to disable the authorization cache, "
            "set cache_ttl to 0."
        )
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"cache_ttl must be an int number of seconds, got {value!r} "
            f"({type(value).__name__}). Omit cache_ttl from configure() to use "
            f"the default of {DEFAULT_CACHE_TTL} seconds, or set it to 0 to "
            "disable the authorization cache."
        )
    if value < 0:
        raise ValueError(
            f"cache_ttl must be 0 or more seconds, got {value!r}. Set it to 0 "
            "to disable the authorization cache; a negative value is not "
            "accepted as another way of saying that."
        )
    return value


class Configuration:
    """
    Singleton configuration for the EndPointBlank library.

    Configure via :func:`end_point_blank.configure`::

        import end_point_blank as epb

        epb.configure(
            client_id="your-client-id",
            client_secret="your-client-secret",
            app_name="my-app",
            environment="production",
        )

    Several settings also fall back to ``ENDPOINTBLANK_*`` environment
    variables when not explicitly configured, in order to support
    zero-code-config deployments. Precedence for each of these settings is:
    explicit value set via :func:`end_point_blank.configure` (or by
    assigning the attribute directly) > the corresponding
    ``ENDPOINTBLANK_*`` environment variable > a built-in default.

    ===============  ===============================
    Attribute        Environment variable
    ===============  ===============================
    ``client_id``      ``ENDPOINTBLANK_CLIENT_ID``
    ``client_secret``  ``ENDPOINTBLANK_CLIENT_SECRET``
    ``base_url``       ``ENDPOINTBLANK_BASE_URL``
    ``log_base_url``   ``ENDPOINTBLANK_LOG_BASE_URL``
    ``app_name``        ``ENDPOINTBLANK_APP_NAME``
    ``environment``     ``ENDPOINTBLANK_ENV``
    ===============  ===============================
    """

    _instance: Optional["Configuration"] = None

    def __new__(cls) -> "Configuration":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_defaults()
        return cls._instance

    def _init_defaults(self) -> None:
        self._client_id: Optional[str] = None
        self._client_secret: Optional[str] = None
        self._base_url: Optional[str] = None
        self._log_base_url: Optional[str] = None
        self._environment: Optional[str] = None
        self._app_name: Optional[str] = None
        self.worker_count: int = 4
        self.log_mode: LogMode = LogMode.DIRECT
        self.version_finder: Optional[Callable] = None
        self.application_version: Optional[str] = None
        self.token_ttl: Optional[int] = None  # seconds
        self._cache_ttl: int = DEFAULT_CACHE_TTL
        self.trust_proxy_headers: bool = True
        self.masking_rules: list[dict] = []
        self.mask_hook: Optional[Callable[[dict, str], dict]] = None

    # ENDPOINTBLANK_*-backed settings.
    #
    # Each property resolves at *read* time (never cached at import) so that
    # an explicitly configured value always wins, then the matching
    # ENDPOINTBLANK_* environment variable, then the built-in default.

    @property
    def client_id(self) -> Optional[str]:
        return self._client_id or os.environ.get("ENDPOINTBLANK_CLIENT_ID")

    @client_id.setter
    def client_id(self, value: Optional[str]) -> None:
        self._client_id = value

    @property
    def client_secret(self) -> Optional[str]:
        return self._client_secret or os.environ.get("ENDPOINTBLANK_CLIENT_SECRET")

    @client_secret.setter
    def client_secret(self, value: Optional[str]) -> None:
        self._client_secret = value

    @property
    def base_url(self) -> str:
        resolved = (
            self._base_url
            or os.environ.get("ENDPOINTBLANK_BASE_URL")
            or "https://in.endpointblank.com"
        )
        return _normalize_base_url(resolved, "base_url")

    @base_url.setter
    def base_url(self, value: Optional[str]) -> None:
        self._base_url = value

    @property
    def log_base_url(self) -> str:
        resolved = (
            self._log_base_url
            or os.environ.get("ENDPOINTBLANK_LOG_BASE_URL")
            or "https://log.endpointblank.com"
        )
        return _normalize_base_url(resolved, "log_base_url")

    @log_base_url.setter
    def log_base_url(self, value: Optional[str]) -> None:
        self._log_base_url = value

    @property
    def app_name(self) -> Optional[str]:
        return self._app_name or os.environ.get("ENDPOINTBLANK_APP_NAME")

    @app_name.setter
    def app_name(self, value: Optional[str]) -> None:
        self._app_name = value

    @property
    def environment(self) -> Optional[str]:
        return self._environment or os.environ.get("ENDPOINTBLANK_ENV")

    @environment.setter
    def environment(self, value: Optional[str]) -> None:
        self._environment = value

    # Validated on assignment (no environment variable fallback).

    @property
    def cache_ttl(self) -> int:
        """Seconds a successful authorization is cached; ``0`` disables the
        cache. Assigning ``None``, a negative number or a non-``int`` raises
        ``ValueError`` -- see :func:`_validate_cache_ttl`."""
        return self._cache_ttl

    @cache_ttl.setter
    def cache_ttl(self, value: int) -> None:
        self._cache_ttl = _validate_cache_ttl(value)

    # URL builders
    @property
    def log_url(self) -> str:
        return f"{self.log_base_url}/api/application_logs"

    @property
    def endpoint_update_url(self) -> str:
        return f"{self.base_url}/api/application_updates"

    @property
    def access_token_url(self) -> str:
        return f"{self.base_url}/api/access_token"

    @property
    def authorize_url(self) -> str:
        return f"{self.base_url}/api/authorize"

    @property
    def application_errors_url(self) -> str:
        return f"{self.log_base_url}/api/application_errors"

    @property
    def requests_url(self) -> str:
        return f"{self.log_base_url}/api/application_requests"

    @property
    def responses_url(self) -> str:
        return f"{self.log_base_url}/api/application_responses"
