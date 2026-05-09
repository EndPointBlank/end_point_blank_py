import os
from typing import Optional

from .configuration import Configuration
from .request_store import RequestStore


class SessionConfiguration:
    """
    Provides session-level configuration derived from the current request,
    such as the runtime environment name.

    Equivalent to the Ruby gem's ``EndPointBlank::SessionConfiguration``.
    """

    @staticmethod
    def env_name() -> str:
        """
        Returns the environment name.

        Resolution order:
        1. ``Configuration.environment`` if set
        2. ``FLASK_ENV`` or ``DJANGO_SETTINGS_MODULE``-derived env from ``os.environ``
        3. ``"production"`` as fallback
        """
        env = Configuration().environment
        if env:
            return env

        for key in ("FLASK_ENV", "APP_ENV", "ENVIRONMENT", "DJANGO_ENV"):
            value = os.environ.get(key)
            if value:
                return value

        return "production"

    @staticmethod
    def environ() -> Optional[dict]:
        """Returns the WSGI environ stored in :class:`~end_point_blank.request_store.RequestStore`."""
        return RequestStore.get()
