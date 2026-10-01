"""Django needs settings configured before ``HttpResponse``/``RequestFactory``
can be imported. That used to live in ``tests/django/conftest.py``, which put it
out of reach of anything outside that directory — including the cross-integration
contract in ``tests/test_integration_contract.py``. It lives here so any test can
exercise the Django integration.

Configuring settings costs nothing when no Django test runs, so there is no
reason to keep it scoped.
"""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=False,
        ALLOWED_HOSTS=["*"],
        DATABASES={},
        INSTALLED_APPS=[],
        SECRET_KEY="test-only",
        DEFAULT_CHARSET="utf-8",
    )
    django.setup()


import pytest


@pytest.fixture(autouse=True)
def _client_credentials(monkeypatch):
    """Every call to intake presents Basic client_id:client_secret, and since
    sc-1469 a missing one raises ``ConfigurationError`` instead of sending
    ``None:None``. Most tests are about something else, so they get a
    credential from the environment by default; a test about the credential
    sets or deletes these itself, and one that configures ``client_id`` wins
    over them."""
    monkeypatch.setenv("ENDPOINTBLANK_CLIENT_ID", "env-client-id")
    monkeypatch.setenv("ENDPOINTBLANK_CLIENT_SECRET", "env-client-secret")
