from unittest.mock import patch

import pytest
from flask import Flask

from end_point_blank.middleware.report_interaction import ReportInteractionMiddleware


def app_with_exception(propagate_exceptions):
    app = Flask(__name__)
    app.config["PROPAGATE_EXCEPTIONS"] = propagate_exceptions

    @app.get("/boom")
    def boom():
        raise RuntimeError("view failed")

    app.wsgi_app = ReportInteractionMiddleware(app.wsgi_app)
    return app


def test_reports_a_flask_exception_when_flask_converts_it_to_a_500():
    app = app_with_exception(False)

    with patch("end_point_blank.middleware.report_interaction.RequestWriter.write"), \
         patch("end_point_blank.middleware.report_interaction.ResponseWriter.write"), \
         patch("end_point_blank.middleware.report_interaction.ExceptionWriter.write") as write:
        response = app.test_client().get("/boom")

    assert response.status_code == 500
    write.assert_called_once()
    assert str(write.call_args.args[0]) == "view failed"


def test_does_not_report_the_same_exception_twice_when_flask_propagates_it():
    app = app_with_exception(True)

    with patch("end_point_blank.middleware.report_interaction.RequestWriter.write"), \
         patch("end_point_blank.middleware.report_interaction.ResponseWriter.write"), \
         patch("end_point_blank.middleware.report_interaction.ExceptionWriter.write") as write:
        with pytest.raises(RuntimeError, match="view failed"):
            app.test_client().get("/boom")

    write.assert_called_once()
