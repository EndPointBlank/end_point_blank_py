from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional

from ..configuration import Configuration

_VND_VERSION = re.compile(r"application/vnd\.\w+\.v(\d+)")
_X_API_VERSION = re.compile(r"v(\d+)")
_PATH_VERSION = re.compile(r"/v(\d+)/")


class VersionFinder:
    """
    Finds the API version from an incoming WSGI request environ by checking
    multiple sources in order:

    1. A custom :attr:`~end_point_blank.configuration.Configuration.version_finder`
       callable if configured.
    2. ``Accept`` header  (e.g. ``application/vnd.api.v1+json``)
    3. ``X-Api-Version`` header (e.g. ``v1``)
    4. ``Content-Type`` header (e.g. ``application/vnd.api.v1+json``)
    5. Query parameter ``version`` (e.g. ``?version=v1``)
    6. URL path segment (e.g. ``/v1/resource``)

    Returns the version number as a string (e.g. ``"1"``) or ``None``.

    Equivalent to the Ruby gem's ``EndPointBlank::Commands::VersionFinder``.
    """

    def find(self, environ: Dict[str, Any]) -> Optional[str]:
        """
        :param environ: The WSGI environ dict for the current request.
        :returns: Version string such as ``"1"``, or ``None`` if not found.
        """
        config = Configuration()

        if config.version_finder:
            return config.version_finder(environ)

        # Accept header
        version = self._extract(environ.get("HTTP_ACCEPT"), _VND_VERSION)
        if version:
            return version

        # X-Api-Version header
        version = self._extract(environ.get("HTTP_X_API_VERSION"), _X_API_VERSION)
        if version:
            return version

        # Content-Type header
        version = self._extract(environ.get("CONTENT_TYPE"), _VND_VERSION)
        if version:
            return version

        # Query parameter
        query = environ.get("QUERY_STRING", "")
        version = self._version_from_query(query)
        if version:
            return version

        # URL path
        version = self._extract(environ.get("PATH_INFO", ""), _PATH_VERSION)
        return version

    @staticmethod
    def _extract(value: Optional[str], pattern: re.Pattern) -> Optional[str]:
        if not value:
            return None
        match = pattern.search(value)
        return match.group(1) if match else None

    @staticmethod
    def _version_from_query(query: str) -> Optional[str]:
        for part in query.split("&"):
            if part.startswith("version="):
                value = part[len("version="):]
                match = _X_API_VERSION.search(value)
                if match:
                    return match.group(1)
        return None
