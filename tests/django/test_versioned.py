"""
``@versioned`` is the marker the Django registrar scans for. It is a separate
implementation from the Flask one, so its contract is pinned separately.
"""

from end_point_blank.django.versioned import versioned


def test_versioned_attaches_the_declared_versions():
    @versioned(["v1", "v2"])
    def my_view(request):
        return "ok"

    assert my_view._epb_versions == ["v1", "v2"]


def test_versioned_returns_the_view_itself_rather_than_a_wrapper():
    # Django binds the returned object to the URL; a wrapper here would change
    # the callback the resolver reports and break the registrar's introspection.
    def my_view(request):
        return "ok"

    assert versioned(["v1"])(my_view) is my_view


def test_stacked_declarations_merge_and_dedupe():
    @versioned(["v1", "v2"])
    @versioned(["v2", "v3"])
    def my_view(request):
        return "ok"

    # Order follows declaration, innermost first, so the manifest stays stable
    # between deploys rather than churning.
    assert my_view._epb_versions == ["v2", "v3", "v1"]


def test_versioned_preserves_the_view_name():
    @versioned(["v1"])
    def student_list(request):
        return "ok"

    assert student_list.__name__ == "student_list"
