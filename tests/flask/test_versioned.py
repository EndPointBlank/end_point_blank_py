from end_point_blank.flask.versioned import versioned


def test_versioned_attaches_metadata():
    @versioned(["v1", "v2"], state="Current")
    def my_view():
        return "ok"

    assert hasattr(my_view, "_epb_versions")
    assert my_view._epb_versions["Current"] == ["v1", "v2"]


def test_versioned_default_state():
    @versioned(["v1"])
    def my_view():
        return "ok"

    assert "__default__" in my_view._epb_versions


def test_versioned_multiple_decorators():
    @versioned(["v1"], state="Deprecated")
    @versioned(["v2", "v3"], state="Current")
    def my_view():
        return "ok"

    assert my_view._epb_versions["Current"] == ["v2", "v3"]
    assert my_view._epb_versions["Deprecated"] == ["v1"]


def test_versioned_preserves_function_name():
    @versioned(["v1"])
    def named_view():
        return "ok"

    assert named_view.__name__ == "named_view"
