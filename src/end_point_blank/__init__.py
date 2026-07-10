"""
EndPointBlank Python Library
============================

Endpoint tracking, authorization, and error reporting for Python web applications.

Quick start::

    import end_point_blank as epb

    epb.configure(
        client_id="your-client-id",
        client_secret="your-client-secret",
        app_name="my-app",
        environment="production",
    )

    # WSGI middleware (works with any WSGI app)
    from end_point_blank.middleware import ReportInteractionMiddleware
    app = ReportInteractionMiddleware(app)

    # Flask decorators
    from end_point_blank.flask import authenticated, authorized, versioned

    # Django middleware: add "end_point_blank.django.ReportInteractionMiddleware"
    # to settings.MIDDLEWARE

"""

from .configuration import Configuration, LogMode
from .unauthorized_error import UnauthorizedError

VERSION = "0.2.2"


def configure(
    *,
    client_id: str = None,
    client_secret: str = None,
    base_url: str = None,
    environment: str = None,
    app_name: str = None,
    worker_count: int = None,
    log_mode: LogMode = None,
    version_finder=None,
    application_version: str = None,
    token_ttl: int = None,
    cache_ttl: int = None,
    masking_rules=None,
    mask_hook=None,
) -> None:
    """
    Configure the EndPointBlank library.

    All parameters are optional; only supplied values are updated.

    :param client_id: Your EndPointBlank client ID.
    :param client_secret: Your EndPointBlank client secret.
    :param base_url: Override the API base URL (default: ``https://endpointblank.com/api``).
    :param environment: Runtime environment name (e.g. ``"production"``).
    :param app_name: Application name reported to the API.
    :param worker_count: Number of background worker threads for delayed writing (default: 4).
    :param log_mode: :class:`~end_point_blank.configuration.LogMode` — ``DIRECT`` or ``DELAYED``.
    :param version_finder: Optional callable ``(environ) -> str | None`` for custom version detection.
    :param application_version: Override application version sent in endpoint updates.
    :param token_ttl: Optional access token TTL in seconds sent to the token endpoint.
    :param cache_ttl: Credential cache TTL in seconds (default: 300).
    :param masking_rules: List of masking rule dicts (``target``/``path``/``regex``/``replacement_value``).
    :param mask_hook: Optional callable ``(payload, record_type) -> payload`` run after rule-based masking.
    """
    config = Configuration()
    if client_id is not None:
        config.client_id = client_id
    if client_secret is not None:
        config.client_secret = client_secret
    if base_url is not None:
        config.base_url = base_url
    if environment is not None:
        config.environment = environment
    if app_name is not None:
        config.app_name = app_name
    if worker_count is not None:
        config.worker_count = worker_count
    if log_mode is not None:
        config.log_mode = log_mode
    if version_finder is not None:
        config.version_finder = version_finder
    if application_version is not None:
        config.application_version = application_version
    if token_ttl is not None:
        config.token_ttl = token_ttl
    if cache_ttl is not None:
        config.cache_ttl = cache_ttl
    if masking_rules is not None:
        config.masking_rules = masking_rules
    if mask_hook is not None:
        config.mask_hook = mask_hook


__all__ = [
    "configure",
    "Configuration",
    "LogMode",
    "UnauthorizedError",
    "VERSION",
]
