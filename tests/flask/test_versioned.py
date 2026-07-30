from end_point_blank.flask.versioned import versioned


def test_versioned_attaches_metadata():
    @versioned(["v1", "v2"])
    def my_view():
        return "ok"

    assert hasattr(my_view, "_epb_versions")
    assert my_view._epb_versions == ["v1", "v2"]


def test_versioned_with_no_versions_is_still_a_declaration():
    # Distinct from never decorating: the endpoint is reported with no version
    # attached, which the portal records as "deployed at no specific version".
    @versioned([])
    def my_view():
        return "ok"

    assert my_view._epb_versions == []


def test_versioned_multiple_decorators_merge_and_dedupe():
    @versioned(["v1", "v2"])
    @versioned(["v2", "v3"])
    def my_view():
        return "ok"

    # Order follows declaration, innermost first, so the manifest stays stable
    # between deploys rather than churning.
    assert my_view._epb_versions == ["v2", "v3", "v1"]


def test_versioned_preserves_function_name():
    @versioned(["v1"])
    def named_view():
        return "ok"

    assert named_view.__name__ == "named_view"
