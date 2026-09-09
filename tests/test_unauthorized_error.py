"""
A refusal has to say *which* refusal it was.

401 and 403 send an integrator to two different places -- "your credential is
wrong" versus "your credential is fine, nobody granted you this endpoint" -- and
for as long as ``UnauthorizedError`` had nowhere to put intake's status, every
Python refusal arrived as a 401 and sent half of them to debug the wrong thing.
The other SDKs each carry the same value under their own name (``statusCode``,
``getStatusCode()``, ``status``); these are the tests for Python's.
"""

import pickle
from unittest.mock import MagicMock

from end_point_blank.unauthorized_error import UnauthorizedError, refusal_from


def response(status=201, payload=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    if payload is None:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = payload
    return resp


class TestTheErrorItself:
    def test_it_carries_the_status_it_was_given(self):
        assert UnauthorizedError("denied", 403).status_code == 403

    def test_the_status_can_be_passed_by_name(self):
        assert UnauthorizedError("denied", status_code=403).status_code == 403

    def test_it_defaults_to_401(self):
        # 401 is the safe default for "a refusal with no status attached", and
        # it is what the JS, Ruby and Java constructors default to as well.
        assert UnauthorizedError("denied").status_code == 401

    def test_the_message_still_works_on_its_own(self):
        # Every existing caller and test constructs it with a message alone.
        # That must keep meaning exactly what it meant before.
        #
        # Guard, not a regression test: this held before the change too. It is
        # here because the change would be worth reverting if it stopped
        # holding.
        error = UnauthorizedError("Authorization failed: nope")

        assert str(error) == "Authorization failed: nope"
        assert error.args == ("Authorization failed: nope",)

    def test_it_can_be_raised_with_no_arguments_at_all(self):
        assert UnauthorizedError().status_code == 401

    def test_the_status_survives_a_pickle_round_trip(self):
        # Exception's default __reduce__ rebuilds from args alone, so without
        # help a 403 crossing a process boundary would come back a 401 --
        # silently, which is the exact failure this attribute exists to remove.
        revived = pickle.loads(pickle.dumps(UnauthorizedError("denied", 403)))

        assert revived.status_code == 403
        assert str(revived) == "denied"


class TestBuildingOneFromIntakesAnswer:
    def test_it_carries_intakes_status_verbatim(self):
        assert refusal_from(response(403), "Authorization").status_code == 403

    def test_a_rejected_credential_stays_a_401(self):
        assert refusal_from(response(401), "Authentication").status_code == 401

    def test_an_intake_failure_is_not_relabelled_as_a_refusal(self):
        # A 500 from intake is not "you are not allowed"; reporting it as 401 or
        # 403 sends the integrator to check a credential that is fine.
        assert refusal_from(response(500), "Authorization").status_code == 500

    def test_no_answer_at_all_is_a_503(self):
        # Nothing refused this caller -- the check could not be made. 401 would
        # blame a credential that was never judged. This is what the other four
        # SDKs already send for the same case.
        error = refusal_from(None, "Authorization")

        assert error.status_code == 503
        assert str(error) == "Authorization failed: Authorization service unavailable"

    def test_the_reason_comes_from_the_body(self):
        error = refusal_from(response(403, payload={"error": "access_denied"}), "Authorization")

        assert str(error) == "Authorization failed: access_denied"
        assert error.status_code == 403

    def test_the_reason_falls_back_to_the_raw_body(self):
        error = refusal_from(response(502, text="Bad Gateway"), "Authorization")

        assert str(error) == "Authorization failed: Bad Gateway"
        assert error.status_code == 502

    def test_the_action_names_which_check_refused(self):
        assert str(refusal_from(response(401, text="nope"), "Authentication")) == (
            "Authentication failed: nope"
        )
