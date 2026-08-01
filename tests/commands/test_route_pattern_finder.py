"""
``RoutePatternFinder`` supplies the ``route`` on every response row. It reads
whatever the framework happened to leave in the environ, so its whole contract
is "find it if it is there, return None otherwise, never raise" — a 404 has no
route and must not become an exception in the middleware's finally block.
"""

from types import SimpleNamespace

from end_point_blank.commands.route_pattern_finder import RoutePatternFinder


class TestFlask:
    def test_finds_the_matched_url_rule(self):
        request = SimpleNamespace(url_rule="/students/<int:student_id>")

        assert RoutePatternFinder.find({"werkzeug.request": request}) == "/students/<int:student_id>"

    def test_returns_none_when_no_rule_matched(self):
        # This is the 404 case: werkzeug leaves the request in place with a null
        # rule, and there is genuinely no route to report.
        request = SimpleNamespace(url_rule=None)

        assert RoutePatternFinder.find({"werkzeug.request": request}) is None

    def test_ignores_a_request_object_with_no_rule_attribute(self):
        assert RoutePatternFinder.find({"werkzeug.request": SimpleNamespace()}) is None

    def test_ignores_a_null_request(self):
        assert RoutePatternFinder.find({"werkzeug.request": None}) is None


class TestDjango:
    def test_finds_the_resolved_route(self):
        request = SimpleNamespace(resolver_match=SimpleNamespace(route="students/<int:student_id>/"))

        assert RoutePatternFinder.find({"django.request": request}) == "students/<int:student_id>/"

    def test_returns_none_when_nothing_resolved(self):
        request = SimpleNamespace(resolver_match=None)

        assert RoutePatternFinder.find({"django.request": request}) is None

    def test_returns_none_for_an_empty_route(self):
        request = SimpleNamespace(resolver_match=SimpleNamespace(route=""))

        assert RoutePatternFinder.find({"django.request": request}) is None

    def test_ignores_a_request_with_no_resolver_attribute(self):
        assert RoutePatternFinder.find({"django.request": SimpleNamespace()}) is None


class TestNeitherFramework:
    def test_returns_none_for_a_bare_environ(self):
        assert RoutePatternFinder.find({"PATH_INFO": "/students"}) is None

    def test_returns_none_for_an_empty_environ(self):
        assert RoutePatternFinder.find({}) is None

    def test_prefers_the_flask_rule_when_both_are_present(self):
        environ = {
            "werkzeug.request": SimpleNamespace(url_rule="/flask/rule"),
            "django.request": SimpleNamespace(resolver_match=SimpleNamespace(route="django/route/")),
        }

        assert RoutePatternFinder.find(environ) == "/flask/rule"
