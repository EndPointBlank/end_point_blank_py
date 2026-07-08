from __future__ import annotations

import os
from enum import Enum
from typing import Callable, Optional


class LogMode(Enum):
    DIRECT = "direct"
    DELAYED = "delayed"


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
        self.cache_ttl: int = 300  # seconds
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
        return (
            self._base_url
            or os.environ.get("ENDPOINTBLANK_BASE_URL")
            or "https://in.endpointblank.com"
        )

    @base_url.setter
    def base_url(self, value: Optional[str]) -> None:
        self._base_url = value

    @property
    def log_base_url(self) -> str:
        return (
            self._log_base_url
            or os.environ.get("ENDPOINTBLANK_LOG_BASE_URL")
            or "https://log.endpointblank.com"
        )

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
    def endpoint_error_url(self) -> str:
        return f"{self.base_url}/api/endpoint_errors"

    @property
    def application_errors_url(self) -> str:
        return f"{self.log_base_url}/api/application_errors"

    @property
    def requests_url(self) -> str:
        return f"{self.log_base_url}/api/application_requests"

    @property
    def responses_url(self) -> str:
        return f"{self.log_base_url}/api/application_responses"
