import base64

from ..configuration import Configuration


class BearerGenerate:
    """
    Generates HTTP Basic Authorization headers using the configured client credentials.

    Creates a Base64-encoded ``client_id:client_secret`` string.
    Equivalent to the Ruby gem's ``EndPointBlank::Commands::BearerGenerate``.
    """

    @staticmethod
    def generate() -> str:
        """Returns the Base64-encoded ``client_id:client_secret`` string."""
        config = Configuration()
        raw = f"{config.client_id}:{config.client_secret}"
        return base64.b64encode(raw.encode()).decode()

    @classmethod
    def auth_header(cls) -> str:
        """Returns a properly formatted ``Basic <credentials>`` header value."""
        return f"Basic {cls.generate()}"
