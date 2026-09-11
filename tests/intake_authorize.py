"""
What intake answers a granted ``POST /api/authorize`` with, for every test that
stands in for intake on that call.

Transcribed from intake, not invented. ``IntakeWeb.AuthorizationController.create/2``
renders ``:show, accesses: [access_map]`` with status 201, and
``IntakeWeb.AuthorizationJSON.show/1`` puts that list under ``data`` --
``accesses`` is the name of the render assign, never a key on the wire. Each
entry carries exactly four keys, which intake's
``authorization_controller_test.exs`` ("POST /api/authorize — authorized") pins
by listing them. ``deprecation`` is added only when the version being called is
deprecated. The values below follow intake's ``authorization_json_test.exs``.

There is one copy on purpose. A stub that answers a shape of its own agrees with
whatever the SDK reads, and that is how an SDK reads a key intake never sends
and still passes its suite: the Elixir SDK read ``accesses`` (sc-463), and this
one read nothing from the grant at all (sc-473).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

SOURCE_ENVIRONMENT_ID = "0b6f7c1e-3a52-4d8e-9f10-5c2a7e4b9d31"


def granted(
    source_application_environment_id: Optional[str] = SOURCE_ENVIRONMENT_ID,
    deprecation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "authorized": True,
        "data": [
            {
                "id": "gen-1",
                "source_application_environment_id": source_application_environment_id,
                "target_application_environment_id": "tgt-env",
                "inserted_at": "2026-01-01T00:00:00Z",
            }
        ],
    }
    if deprecation is not None:
        body["deprecation"] = deprecation
    return body
