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
