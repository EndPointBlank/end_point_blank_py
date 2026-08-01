"""
The registrar publishes the endpoint manifest. Everything downstream — the
portal's endpoint list, and the authorize call's ability to find a matching row —
depends on it emitting one entry per (path, method) that the app actually serves.

Version discovery is the subtle part: Django apps commonly bind one dispatcher
to a URL and forward to per-method inner functions, so the decorator is rarely
on the callback the resolver hands back.
"""

import functools
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.http import HttpResponse
from django.urls import include, path
from django.views.decorators.http import require_http_methods

from end_point_blank.django.endpoint_registrar import _collect_endpoints, register_django_endpoints
from end_point_blank.django.versioned import versioned


def resolver(*patterns):
    return SimpleNamespace(url_patterns=list(patterns))


class ExplodingPattern:
    """Stands in for a URL entry whose own repr blows up mid-scan."""

    def __str__(self):
        raise RuntimeError("unreadable pattern")


@versioned(["v1"])
def student_list(request):
    return HttpResponse("ok")


@versioned(["v2"])
def student_create(request):
    return HttpResponse("ok")


def student_dispatch(request):
    """The pattern the bytecode scan exists for: the URL binds this, but the
    versions live on the functions it forwards to."""
    if request.method == "POST":
        return student_create(request)
    return student_list(request)


def undecorated(request):
    return HttpResponse("ok")


class TestWhichRoutesAreCollected:
    def test_a_versioned_route_is_collected(self):
        endpoints = _collect_endpoints(resolver(path("students/", student_list)))

        assert len(endpoints) == 1
        assert endpoints[0]["endpoint_versions"] == ["v1"]

    def test_a_route_with_no_version_declaration_is_skipped(self):
        # An undeclared view is one the developer has not opted in; publishing it
        # would fill the portal's manifest with Django's own plumbing.
        assert _collect_endpoints(resolver(path("students/", undecorated))) == []

    def test_a_dunder_route_is_skipped(self):
        assert _collect_endpoints(resolver(path("__debug__/", student_list))) == []

    def test_an_unrecognised_url_entry_is_ignored(self):
        endpoints = _collect_endpoints(resolver(SimpleNamespace(), path("students/", student_list)))

        assert len(endpoints) == 1

    def test_a_pattern_that_raises_while_being_read_does_not_abort_the_scan(self):
        # One broken third-party URL entry must not cost the app its entire
        # manifest — the rest of the routes still have to be published.
        broken = path("broken/", student_list)
        broken.pattern = ExplodingPattern()

        endpoints = _collect_endpoints(resolver(broken, path("students/", student_list)))

        assert [e["path"] for e in endpoints] == ["/students/"]

    def test_a_view_that_is_not_a_plain_function_is_skipped_rather_than_crashing(self):
        # Class-based and third-party callables have no bytecode to scan; the
        # scan has to give up on them, not take the whole manifest down.
        class CallableView:
            def __call__(self, request):
                return HttpResponse("ok")

        assert _collect_endpoints(resolver(path("students/", CallableView()))) == []


class TestThePathsEmitted:
    def test_django_converter_syntax_never_reaches_the_wire(self):
        # NOTE: this asserts only that "<int:...>" is rewritten. The registrar
        # emits ":student_id" while the @authorized decorator sends
        # "{student_id}" for the same route — see the coverage report.
        endpoints = _collect_endpoints(resolver(path("students/<int:student_id>/", student_list)))

        assert "<int:" not in endpoints[0]["path"]
        assert endpoints[0]["path"].startswith("/students/")

    def test_the_path_is_rooted(self):
        endpoints = _collect_endpoints(resolver(path("students/", student_list)))

        assert endpoints[0]["path"] == "/students/"

    def test_an_included_urlconf_keeps_its_prefix(self):
        included = include(([path("students/", student_list)], "app"))

        endpoints = _collect_endpoints(resolver(path("api/", included)))

        assert endpoints[0]["path"] == "/api/students/"

    def test_nesting_composes_prefixes(self):
        inner = include(([path("students/", student_list)], "inner"))
        outer = include(([path("v1/", inner)], "outer"))

        endpoints = _collect_endpoints(resolver(path("api/", outer)))

        assert endpoints[0]["path"] == "/api/v1/students/"


class TestTheHttpMethodsEmitted:
    def test_defaults_to_get_when_the_view_declares_no_restriction(self):
        endpoints = _collect_endpoints(resolver(path("students/", student_list)))

        assert [e["http_method"] for e in endpoints] == ["GET"]

    def test_emits_one_entry_per_declared_method(self):
        # The runtime authorize call knows the real verb, so a route registered
        # only under GET denies every POST to the same path.
        @require_http_methods(["GET", "POST"])
        @versioned(["v1"])
        def view(request):
            return HttpResponse("ok")

        endpoints = _collect_endpoints(resolver(path("students/", view)))

        assert sorted(e["http_method"] for e in endpoints) == ["GET", "POST"]

    def test_an_unrelated_decorator_in_the_chain_is_stepped_over(self):
        # Real apps stack login_required, cache_page and their own decorators
        # around views. Each adds a closure of its own, and the method lookup has
        # to keep walking rather than stopping at the first one it does not know.
        def audited(view_func):
            audit_log = []

            @functools.wraps(view_func)
            def wrapper(request, *args, **kwargs):
                audit_log.append(request)
                return view_func(request, *args, **kwargs)

            return wrapper

        @audited
        @require_http_methods(["DELETE"])
        @versioned(["v1"])
        def view(request):
            return HttpResponse("ok")

        endpoints = _collect_endpoints(resolver(path("students/", view)))

        assert [e["http_method"] for e in endpoints] == ["DELETE"]

    def test_the_methods_are_upper_cased(self):
        @require_http_methods(["get"])
        @versioned(["v1"])
        def view(request):
            return HttpResponse("ok")

        endpoints = _collect_endpoints(resolver(path("students/", view)))

        assert endpoints[0]["http_method"] == "GET"

    def test_every_entry_for_a_route_carries_the_same_versions(self):
        @require_http_methods(["GET", "POST"])
        @versioned(["v1", "v2"])
        def view(request):
            return HttpResponse("ok")

        endpoints = _collect_endpoints(resolver(path("students/", view)))

        assert all(e["endpoint_versions"] == ["v1", "v2"] for e in endpoints)


class TestVersionDiscovery:
    def test_finds_versions_declared_on_the_view_itself(self):
        endpoints = _collect_endpoints(resolver(path("students/", student_list)))

        assert endpoints[0]["endpoint_versions"] == ["v1"]

    def test_looks_through_a_wrapping_decorator(self):
        # @require_http_methods and friends replace the callback with a wrapper;
        # without following __wrapped__ every decorated view looks undeclared.
        @require_http_methods(["GET"])
        @versioned(["v3"])
        def view(request):
            return HttpResponse("ok")

        endpoints = _collect_endpoints(resolver(path("students/", view)))

        assert endpoints[0]["endpoint_versions"] == ["v3"]

    def test_aggregates_versions_from_the_functions_a_dispatcher_forwards_to(self):
        endpoints = _collect_endpoints(resolver(path("students/", student_dispatch)))

        # Order follows the order the dispatcher references them in, which is an
        # implementation detail of the scan; what matters is that neither is lost.
        assert sorted(endpoints[0]["endpoint_versions"]) == ["v1", "v2"]

    def test_a_dispatcher_that_also_calls_builtins_still_finds_the_versions(self):
        # The scan walks every global name the dispatcher references; anything
        # that is not a declared view — builtins, helpers, imports — has to be
        # stepped over rather than treated as a version source.
        def dispatch(request):
            if len(request.method) > 3:
                return student_create(request)
            return student_list(request)

        endpoints = _collect_endpoints(resolver(path("students/", dispatch)))

        assert sorted(endpoints[0]["endpoint_versions"]) == ["v1", "v2"]

    def test_a_declaration_on_the_dispatcher_wins_over_the_scan(self):
        @versioned(["v9"])
        def dispatch(request):
            return student_list(request)

        endpoints = _collect_endpoints(resolver(path("students/", dispatch)))

        assert endpoints[0]["endpoint_versions"] == ["v9"]


class TestPublishing:
    def test_sends_the_collected_endpoints(self):
        urlconf = resolver(path("students/", student_list))

        with patch("django.urls.get_resolver", return_value=urlconf):
            with patch("end_point_blank.django.endpoint_registrar.EndpointUpdate") as update:
                register_django_endpoints()

        sent = update.send_update.call_args[0][0]
        assert sent[0]["path"] == "/students/"

    def test_publishes_an_empty_manifest_rather_than_nothing(self):
        # Sending [] is how an app tells the portal it now serves no endpoints;
        # skipping the call would leave stale rows visible forever.
        with patch("django.urls.get_resolver", return_value=resolver()):
            with patch("end_point_blank.django.endpoint_registrar.EndpointUpdate") as update:
                register_django_endpoints()

        update.send_update.assert_called_once_with([])
