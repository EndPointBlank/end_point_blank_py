"""Django needs settings configured before ``HttpResponse``/``RequestFactory``
can be imported, and the SDK's Django integration is exercised against real
Django objects rather than stand-ins."""

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
