"""
``normalize_route_pattern`` is the SDK's only spelling of "turn a framework
route pattern into the path intake stores".

There used to be three: ``flask/authorized.py`` had one, ``django/decorators.py``
had a byte-identical copy, and ``flask/authenticated.py`` had none at all and
sent the concrete URL instead. That third case is sc-342 -- intake's
``Intake.Apis.get_endpoint/3`` matches ``path`` with SQL ``=``, so
``/students/42`` resolved to no row and the caller was refused for a grant they
held.

``TestAgreementWithIntake`` is the guard sc-302 asked for: the two sides of a
path have to canonicalize to the same key, or the bug returns wearing a
different hat.
"""

import re

import pytest

from end_point_blank.commands.route_path import normalize_route_pattern

# Intake's ``Intake.PathNormalizer``, transcribed from
# intake/lib/intake/path_normalizer.ex. Its canonical form is ``:name``, not
# ``{name}``: it rewrites ``{name}`` and ``{name:regex}`` and touches nothing
# else -- no leading slash, no trailing slash, no case folding.
#
# Transcribed rather than imported, because intake is an Elixir application and
# nothing here may reach into it. TestAgreementWithIntake pins the transcription
# against the fixtures intake's own tests use, so this cannot quietly drift into
# a test of a regex invented here.
_INTAKE_PATH_VAR = re.compile(r"\{([^/}:]+)(?::[^}]*)?\}")


def intake_normalize(path):
    return _INTAKE_PATH_VAR.sub(r":\1", path)


class TestTheConversion:
    def test_rewrites_a_typed_converter(self):
        assert normalize_route_pattern("/students/<int:student_id>") == "/students/{student_id}"

    def test_rewrites_an_untyped_placeholder(self):
        assert normalize_route_pattern("/students/<student_id>") == "/students/{student_id}"

    def test_rewrites_every_placeholder_in_the_pattern(self):
        assert normalize_route_pattern(
            "/classes/<int:class_id>/students/<name>"
        ) == "/classes/{class_id}/students/{name}"

    def test_rewrites_a_converter_carrying_arguments(self):
        assert normalize_route_pattern("/codes/<string(length=4):code>") == "/codes/{code}"

    def test_leaves_a_static_path_alone(self):
        assert normalize_route_pattern("/health") == "/health"

    def test_is_idempotent(self):
        once = normalize_route_pattern("/students/<int:student_id>")

        assert normalize_route_pattern(once) == once


class TestTheLeadingSlash:
    """Django's ``resolver_match.route`` has no leading slash and Flask's
    ``url_rule`` always does. Intake adds neither, so the SDK has to."""

    def test_supplies_the_slash_django_omits(self):
        assert normalize_route_pattern("students/<int:student_id>/") == "/students/{student_id}/"

    def test_does_not_double_the_slash_flask_supplies(self):
        assert normalize_route_pattern("/students/<int:student_id>") == "/students/{student_id}"

    def test_the_root_path_survives(self):
        assert normalize_route_pattern("/") == "/"

    def test_an_empty_pattern_becomes_the_root_path(self):
        assert normalize_route_pattern("") == "/"


class TestWhatItDoesNotDo:
    """Intake trims no trailing slash and folds no case, so neither may the
    SDK: a path the SDK "tidied" is a path ``get_endpoint/3`` cannot find."""

    def test_keeps_a_trailing_slash(self):
        assert normalize_route_pattern("/students/") == "/students/"

    def test_keeps_the_case_it_was_given(self):
        assert normalize_route_pattern("/Students/<int:Student_Id>") == "/Students/{Student_Id}"


class TestAgreementWithIntake:
    """The path a decorator sends and the path the registrar published have to
    land on the same key once intake has normalized them. Intake normalizes both
    sides -- ``authorization_controller.ex:20`` on the way in and ``apis.ex:33``
    at registration -- so what matters is not that the SDK emits intake's
    spelling but that both SDK paths survive intake's normalizer identically.
    """

    @pytest.mark.parametrize(
        "pattern,canonical",
        [
            ("/students/<int:student_id>", "/students/:student_id"),
            ("/students/<student_id>", "/students/:student_id"),
            ("/classes/<int:class_id>/students/<name>", "/classes/:class_id/students/:name"),
            ("/health", "/health"),
        ],
    )
    def test_the_sdk_form_canonicalizes_to_intakes_form(self, pattern, canonical):
        assert intake_normalize(normalize_route_pattern(pattern)) == canonical

    def test_the_raw_framework_pattern_does_not(self):
        # Intake's normalizer knows nothing about angle brackets; they pass
        # through byte-identical and match no registered row. So "let intake
        # sort it out" is not an option the SDK has.
        assert intake_normalize("/students/<int:student_id>") == "/students/<int:student_id>"

    def test_a_concrete_url_canonicalizes_to_itself(self):
        # The whole of sc-342 in one line: what @authenticated used to send
        # survives intake's normalizer unchanged, and no endpoint row is
        # spelled "/students/42".
        assert intake_normalize("/students/42") == "/students/42"


class TestTheTranscribedIntakeNormalizer:
    """Guards on the transcription itself, not on the SDK. These are the
    fixtures intake's own tests assert (endpoint_sync_controller_test.exs:278,
    application_response_controller_test.exs:195, spy_coverage_test.exs:35)."""

    def test_rewrites_the_braced_form(self):
        assert intake_normalize("/users/{id}") == "/users/:id"

    def test_drops_a_regex_constraint(self):
        assert intake_normalize("/users/{id:[0-9]+}") == "/users/:id"

    def test_leaves_the_canonical_form_alone(self):
        assert intake_normalize("/users/:id") == "/users/:id"
