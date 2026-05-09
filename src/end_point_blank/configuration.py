from __future__ import annotations

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
    """

    _instance: Optional["Configuration"] = None

    def __new__(cls) -> "Configuration":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_defaults()
        return cls._instance

    def _init_defaults(self) -> None:
        self.client_id: Optional[str] = None
        self.client_secret: Optional[str] = None
        self.base_url: str = "https://in.endpointblank.com"
        self.log_base_url: str = "https://log.endpointblank.com"
        self.environment: Optional[str] = None
        self.app_name: Optional[str] = None
        self.worker_count: int = 4
        self.log_mode: LogMode = LogMode.DIRECT
        self.version_finder: Optional[Callable] = None
        self.application_version: Optional[str] = None
        self.token_ttl: Optional[int] = None  # seconds
        self.cache_ttl: int = 300  # seconds

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
