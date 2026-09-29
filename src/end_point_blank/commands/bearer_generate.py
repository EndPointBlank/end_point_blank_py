import base64
import warnings

from ..configuration import Configuration

_DEPRECATED = (
    "{} is deprecated: its header carries this service's own "
    "client_id/client_secret and is only valid for this service's own "
    "EndPointBlank intake. Never send it to a provider; use "
    "Authorization.header(base_url) (sc-1469)."
)


class BearerGenerate:
    """
    Generates HTTP Basic Authorization headers using the configured client credentials.

    Creates a Base64-encoded ``client_id:client_secret`` string.
    Equivalent to the Ruby gem's ``EndPointBlank::Commands::BearerGenerate``.

    .. deprecated::
        The header carries this service's own client secret and is only valid for
        this service's own EndPointBlank intake. Never send it to a provider; use
        ``Authorization.header(base_url)`` (sc-1469).
    """

    @staticmethod
    def generate() -> str:
        """Returns the Base64-encoded ``client_id:client_secret`` string.

        Emits a :class:`DeprecationWarning`.
        """
        warnings.warn(_DEPRECATED.format("BearerGenerate.generate"), DeprecationWarning, stacklevel=2)
        return BearerGenerate._credentials()

    @classmethod
    def auth_header(cls) -> str:
        """Returns a properly formatted ``Basic <credentials>`` header value.

        Emits a :class:`DeprecationWarning`.
        """
        warnings.warn(_DEPRECATED.format("BearerGenerate.auth_header"), DeprecationWarning, stacklevel=2)
        return f"Basic {cls._credentials()}"

    @staticmethod
    def _credentials() -> str:
        config = Configuration()
        raw = f"{config.client_id}:{config.client_secret}"
        return base64.b64encode(raw.encode()).decode()
