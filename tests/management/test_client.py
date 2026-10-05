"""
``ManagementClient`` against a stubbed HTTP layer (``responses``): every
resource method's method, path, query and body; pagination; Idempotency-Key
generation and reuse; retries; error mapping; and that the only credential
ever sent is the management key, as a Bearer token.
"""
import json
import logging
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
import responses

import end_point_blank as epb
from end_point_blank import VERSION, ConfigurationError
from end_point_blank.configuration import Configuration
from end_point_blank.management import (
    ApiPackage,
    Application,
    ApplicationEnvironment,
    AuthenticationError,
    BadRequestError,
    ClaimInvite,
    Client,
    ConflictError,
    Credential,
    Deleted,
    Endpoint,
    Environment,
    ErrorCode,
    Grant,
    IdempotencyReplayUnavailableError,
    InsufficientScopeError,
    InvalidResponseError,
    ManagedClient,
    ManagementApiError,
    ManagementClient,
    ManagementConnectionError,
    NotFoundError,
    Organization,
    PackageAssignment,
    PackageEndpoint,
    PackageEndpointAdded,
    Page,
    PlanLimitError,
    RateLimitedError,
    RequestRefusedError,
    ServerError,
    ServiceUnavailableError,
    ValidationFailedError,
)

KEY = "epb_mk_test_9f8e7d6c5b4a"
BASE = "https://portal.test"
API = BASE + "/api/v1"
RUNTIME_ID = "acme.runtime-client-id"
RUNTIME_SECRET = "runtime-secret-never-sent"


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def mgmt(sleeps):
    return ManagementClient(KEY, base_url=BASE, sleep=sleeps.append)


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=True) as mock:
        yield mock


@pytest.fixture(autouse=True)
def _runtime_credentials():
    """A runtime credential is configured for every test, so any test that
    sends a request also shows it is never sent to the management API."""
    config = Configuration()
    config._init_defaults()
    epb.configure(client_id=RUNTIME_ID, client_secret=RUNTIME_SECRET)
    yield
    config._init_defaults()


def request(rsps, i=-1):
    return rsps.calls[i].request


def path_of(req):
    return urlsplit(req.url).path


def query_of(req):
    return {k: v[0] if len(v) == 1 else v for k, v in parse_qs(urlsplit(req.url).query).items()}


def body_of(req):
    return json.loads(req.body) if req.body else None


def error_body(code, message="msg", details=None):
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


# --- construction and the key -------------------------------------------------


class TestConstruction:
    @pytest.mark.parametrize(
        "key",
        [
            None, "", "   ", "epb_mk_", "abc", "Basic abc", RUNTIME_SECRET, 123, "epb_xx_123",
            "epb_mk_SECRETPART\nx", "epb_mk_SECRET\r\nInjected: 1", "epb_mk_SECRET PART",
            "epb_mk_SECRET\tPART", "epb_mk_SECRET+PART/=", "epb_mk_SECRÉT",
        ],
    )
    def test_refuses_anything_but_a_management_key(self, key):
        with pytest.raises(ConfigurationError) as info:
            ManagementClient(key)
        text = str(info.value) + repr(info.value) + repr(info.value.args)
        assert "epb_mk_" in text
        if isinstance(key, str) and key.strip() and key != "epb_mk_":
            assert key not in text
        assert "SECRET" not in text

    def test_accepts_a_key_as_app_portal_mints_it(self):
        ManagementClient("epb_mk_" + "Ab9-_" * 8)

    def test_defaults_to_the_portal(self):
        assert ManagementClient(KEY).base_url == "https://app.endpointblank.com"

    def test_base_url_is_overridable_and_trailing_slash_dropped(self):
        assert ManagementClient(KEY, base_url="http://localhost:4000/").base_url == "http://localhost:4000"

    @pytest.mark.parametrize(
        "base_url", ["ftp://x", "localhost:4000", "https://", "https://u:p@h", "https://h?q=1", None]
    )
    def test_refuses_a_bad_base_url(self, base_url):
        with pytest.raises(ValueError):
            ManagementClient(KEY, base_url=base_url)

    @pytest.mark.parametrize("value", [-1, 1.5, True, "2"])
    def test_refuses_a_bad_max_retries(self, value):
        with pytest.raises(ValueError):
            ManagementClient(KEY, max_retries=value)

    def test_repr_redacts_the_key(self, mgmt):
        for text in (repr(mgmt), str(mgmt), repr(mgmt._transport), repr(mgmt.for_managed_client("c1"))):
            assert KEY not in text
            assert "test_9f8e" not in text
        assert "redacted" in repr(mgmt)

    def test_surrounding_whitespace_in_the_key_is_dropped(self, rsps):
        rsps.get(API + "/organization", json={"data": {"id": "o1"}})
        ManagementClient("  " + KEY + "\n", base_url=BASE).get_organization()
        assert request(rsps).headers["Authorization"] == f"Bearer {KEY}"


# --- what is sent -----------------------------------------------------------


class TestHeaders:
    def test_get_sends_bearer_key_accept_and_user_agent(self, mgmt, rsps):
        rsps.get(API + "/organization", json={"data": {"id": "o1"}})
        mgmt.get_organization()
        headers = request(rsps).headers
        assert headers["Authorization"] == f"Bearer {KEY}"
        assert headers["Accept"] == "application/json"
        assert headers["User-Agent"].startswith(f"end-point-blank-py/{VERSION}")
        assert "Content-Type" not in headers
        assert "Idempotency-Key" not in headers

    def test_a_body_is_json(self, mgmt, rsps):
        rsps.post(API + "/api_packages", json={"data": {"id": "p1"}}, status=201)
        mgmt.create_api_package("Gold")
        assert request(rsps).headers["Content-Type"] == "application/json"

    def test_runtime_credentials_are_never_sent(self, mgmt, rsps):
        rsps.get(API + "/clients", json={"data": [], "next_cursor": None})
        rsps.post(API + "/credentials", json={"data": {"id": "c1", "client_secret": "s"}}, status=201)
        rsps.patch(API + "/environments/e1", json={"data": {"id": "e1"}})
        rsps.delete(API + "/clients/c1", json={"data": {"id": "c1", "deleted": True}})
        mgmt.list_clients()
        mgmt.create_credential("ae1")
        mgmt.update_environment("e1", name="Staging")
        mgmt.delete_client("c1")

        for call in rsps.calls:
            req = call.request
            assert req.headers["Authorization"] == f"Bearer {KEY}"
            body = req.body or ""
            everything = req.url + str(dict(req.headers)) + (body.decode() if isinstance(body, bytes) else body)
            assert RUNTIME_ID not in everything
            assert RUNTIME_SECRET not in everything
            assert "Basic" not in everything
            assert "x-epb-sdk" not in {k.lower() for k in req.headers}

    def test_ids_are_escaped_into_one_path_segment(self, mgmt, rsps):
        rsps.get(API + "/applications/a%2F..%2Fx", json={"data": {"id": "x"}})
        mgmt.get_application("a/../x")
        assert urlsplit(request(rsps).url).path == "/api/v1/applications/a%2F..%2Fx"

    @pytest.mark.parametrize("bad", [None, "", "  ", 5])
    def test_refuses_an_empty_id(self, mgmt, bad):
        with pytest.raises(ValueError):
            mgmt.get_application(bad)

    # rsps has no routes registered, so any request sent would fail the test.
    @pytest.mark.parametrize("dots", [".", "..", "..."])
    @pytest.mark.parametrize("call", [
        lambda m, d: m.delete_client_grant("c1", d),
        lambda m, d: m.unassign_package("c1", d),
        lambda m, d: m.remove_package_endpoint("p1", d),
        lambda m, d: m.delete_application_environment("a1", d),
        lambda m, d: m.delete_client(d),
        lambda m, d: m.revoke_credential(d),
        lambda m, d: m.for_managed_client(d),
        lambda m, d: m.for_managed_client("mc1").delete_application_environment("a1", d),
    ], ids=["grant", "assignment", "package-endpoint", "app-env", "client", "credential", "managed-client",
            "managed-app-env"])
    def test_refuses_a_dot_segment_id_without_a_request(self, mgmt, rsps, call, dots):
        with pytest.raises(ValueError):
            call(mgmt, dots)
        assert len(rsps.calls) == 0

    def test_ids_with_dots_inside_are_fine(self, mgmt, rsps):
        rsps.get(API + "/applications/v1.2..3", json={"data": {"id": "v1.2..3"}})
        assert mgmt.get_application("v1.2..3").id == "v1.2..3"

    def test_accepts_a_uuid_id(self, mgmt, rsps):
        some = uuid.uuid4()
        rsps.get(API + f"/environments/{some}", json={"data": {"id": str(some)}})
        assert mgmt.get_environment(some).id == str(some)


# --- every resource method --------------------------------------------------

ONE = {"data": {"id": "x1"}}
PAGE = {"data": [{"id": "x1"}, {"id": "x2"}], "next_cursor": "cur2"}
DELETED = {"data": {"id": "x1", "deleted": True}}

# (name, call, http method, path, query, body, status, answer, result type)
CASES = [
    ("get_organization", lambda m: m.get_organization(), "GET", "/organization", {}, None, 200,
     {"data": {"id": "o1", "name": "Acme", "domain": "acme.test", "slug": "acme", "key": {"name": "ci", "scope": "write"}}},
     Organization),
    # API packages
    ("list_api_packages", lambda m: m.list_api_packages(limit=2, after="c1"), "GET", "/api_packages",
     {"limit": "2", "after": "c1"}, None, 200, PAGE, Page),
    ("get_api_package", lambda m: m.get_api_package("p1"), "GET", "/api_packages/p1", {}, None, 200, ONE, ApiPackage),
    ("create_api_package", lambda m: m.create_api_package("Gold"), "POST", "/api_packages", {}, {"name": "Gold"}, 201,
     ONE, ApiPackage),
    ("update_api_package", lambda m: m.update_api_package("p1", name="Silver"), "PATCH", "/api_packages/p1", {},
     {"name": "Silver"}, 200, ONE, ApiPackage),
    ("delete_api_package", lambda m: m.delete_api_package("p1"), "DELETE", "/api_packages/p1", {}, None, 200, DELETED,
     Deleted),
    ("list_package_endpoints", lambda m: m.list_package_endpoints("p1"), "GET", "/api_packages/p1/endpoints", {}, None,
     200, PAGE, Page),
    ("add_package_endpoint",
     lambda m: m.add_package_endpoint("p1", application_id="a1", environment_id="e1", endpoint_id="ep1"),
     "POST", "/api_packages/p1/endpoints", {}, {"application_id": "a1", "environment_id": "e1", "endpoint_id": "ep1"},
     201, {"data": {"id": "acc1", "all_endpoints": False}, "warnings": []}, PackageEndpointAdded),
    ("add_package_endpoint all endpoints",
     lambda m: m.add_package_endpoint("p1", application_id="a1", environment_id="e1"),
     "POST", "/api_packages/p1/endpoints", {}, {"application_id": "a1", "environment_id": "e1"},
     201, {"data": {"id": "acc1", "all_endpoints": True}, "warnings": []}, PackageEndpointAdded),
    ("remove_package_endpoint", lambda m: m.remove_package_endpoint("p1", "acc1"), "DELETE",
     "/api_packages/p1/endpoints/acc1", {}, None, 200, {"data": {"id": "acc1", "deleted": True}, "warnings": []},
     Deleted),
    # endpoint lookup
    ("list_endpoints", lambda m: m.list_endpoints(application_id="a1", version="1.0.0", limit=10), "GET", "/endpoints",
     {"application_id": "a1", "version": "1.0.0", "limit": "10"}, None, 200, PAGE, Page),
    # clients
    ("list_clients", lambda m: m.list_clients(), "GET", "/clients", {}, None, 200, PAGE, Page),
    ("get_client", lambda m: m.get_client("c1"), "GET", "/clients/c1", {}, None, 200, ONE, Client),
    ("create_client invite",
     lambda m: m.create_client(
         "Globex",
         contacts=[{"email": "a@globex.test", "first_name": "A", "last_name": "B"}],
         packages=[{"api_package_id": "p1", "environment_id": "e1"}],
         grants=[{"target_application_id": "a1", "environment_id": "e1"}],
     ),
     "POST", "/clients", {},
     {"name": "Globex", "contacts": [{"email": "a@globex.test", "first_name": "A", "last_name": "B"}],
      "packages": [{"api_package_id": "p1", "environment_id": "e1"}],
      "grants": [{"target_application_id": "a1", "environment_id": "e1"}]},
     201, {"data": {"id": "c1", "status": "pending", "invite_code": "INV"}}, Client),
    ("create_managed_client", lambda m: m.create_managed_client("Initech"), "POST", "/clients", {},
     {"name": "Initech", "managed": True}, 201, {"data": {"id": "c1", "managed": True, "status": "accepted"}}, Client),
    ("delete_client", lambda m: m.delete_client("c1"), "DELETE", "/clients/c1", {}, None, 200, DELETED, Deleted),
    ("send_claim_invite", lambda m: m.send_claim_invite("c1", "owner@initech.test"), "POST",
     "/clients/c1/claim_invites", {}, {"email": "owner@initech.test"}, 201,
     {"data": {"client_id": "c1", "email": "owner@initech.test"}}, ClaimInvite),
    ("send_claim_invite with return_to",
     lambda m: m.send_claim_invite("c1", "owner@initech.test", return_to="https://app.example.test/welcome"),
     "POST", "/clients/c1/claim_invites", {},
     {"email": "owner@initech.test", "return_to": "https://app.example.test/welcome"}, 201,
     {"data": {"client_id": "c1", "email": "owner@initech.test"}}, ClaimInvite),
    # package assignments
    ("list_client_packages", lambda m: m.list_client_packages("c1"), "GET", "/clients/c1/packages", {}, None, 200,
     PAGE, Page),
    ("assign_package", lambda m: m.assign_package("c1", api_package_id="p1", environment_id="e1"), "POST",
     "/clients/c1/packages", {}, {"api_package_id": "p1", "environment_id": "e1"}, 201,
     {"data": {"id": "as1", "status": "active"}}, PackageAssignment),
    ("update_client_package", lambda m: m.update_client_package("c1", "as1", environment_id="e2"), "PATCH",
     "/clients/c1/packages/as1", {}, {"environment_id": "e2"}, 200, ONE, PackageAssignment),
    ("unassign_package", lambda m: m.unassign_package("c1", "as1"), "DELETE", "/clients/c1/packages/as1", {}, None,
     200, DELETED, Deleted),
    # direct grants
    ("list_client_grants", lambda m: m.list_client_grants("c1"), "GET", "/clients/c1/grants", {}, None, 200, PAGE,
     Page),
    ("create_client_grant",
     lambda m: m.create_client_grant("c1", target_application_id="a1", environment_id="e1", target_endpoint_id="ep1"),
     "POST", "/clients/c1/grants", {}, {"target_application_id": "a1", "environment_id": "e1",
                                       "target_endpoint_id": "ep1"}, 201, ONE, Grant),
    ("delete_client_grant", lambda m: m.delete_client_grant("c1", "g1"), "DELETE", "/clients/c1/grants/g1", {}, None,
     200, {"data": {"id": "g1", "deleted": True, "still_granted_by_package": True}}, Deleted),
]

# Served for the key's organization and, under /clients/<id>, a managed client's.
ORG_CASES = [
    ("list_applications", lambda m: m.list_applications(limit=5), "GET", "/applications", {"limit": "5"}, None, 200,
     PAGE, Page),
    ("get_application", lambda m: m.get_application("a1"), "GET", "/applications/a1", {}, None, 200, ONE, Application),
    ("create_application",
     lambda m: m.create_application("Orders", {"e1": "https://orders.test"}, public=False),
     "POST", "/applications", {}, {"name": "Orders", "environment_base_urls": {"e1": "https://orders.test"},
                                   "public": False}, 201, ONE, Application),
    ("update_application", lambda m: m.update_application("a1", name="Orders v2"), "PATCH", "/applications/a1", {},
     {"name": "Orders v2"}, 200, ONE, Application),
    ("delete_application", lambda m: m.delete_application("a1"), "DELETE", "/applications/a1", {}, None, 200, DELETED,
     Deleted),
    ("list_application_environments", lambda m: m.list_application_environments("a1"), "GET",
     "/applications/a1/environments", {}, None, 200, PAGE, Page),
    ("create_application_environment",
     lambda m: m.create_application_environment("a1", "e1", "https://orders.test"), "POST",
     "/applications/a1/environments", {}, {"environment_id": "e1", "base_url": "https://orders.test"}, 201, ONE,
     ApplicationEnvironment),
    ("delete_application_environment", lambda m: m.delete_application_environment("a1", "ae1"), "DELETE",
     "/applications/a1/environments/ae1", {}, None, 200, DELETED, Deleted),
    ("list_environments", lambda m: m.list_environments(), "GET", "/environments", {}, None, 200, PAGE, Page),
    ("get_environment", lambda m: m.get_environment("e1"), "GET", "/environments/e1", {}, None, 200, ONE, Environment),
    ("create_environment", lambda m: m.create_environment("Staging", "staging.test", is_default=False), "POST",
     "/environments", {}, {"name": "Staging", "domain": "staging.test", "is_default": False}, 201, ONE, Environment),
    ("update_environment", lambda m: m.update_environment("e1", domain="stage.test"), "PATCH", "/environments/e1", {},
     {"domain": "stage.test"}, 200, ONE, Environment),
    ("delete_environment", lambda m: m.delete_environment("e1"), "DELETE", "/environments/e1", {}, None, 200, DELETED,
     Deleted),
    ("list_credentials", lambda m: m.list_credentials(application_environment_id="ae1"), "GET", "/credentials",
     {"application_environment_id": "ae1"}, None, 200, PAGE, Page),
    ("get_credential", lambda m: m.get_credential("cr1"), "GET", "/credentials/cr1", {}, None, 200, ONE, Credential),
    ("create_credential", lambda m: m.create_credential("ae1"), "POST", "/credentials", {},
     {"application_environment_id": "ae1"}, 201, {"data": {"id": "cr1", "client_secret": "sek"}}, Credential),
    ("rotate_credential", lambda m: m.rotate_credential("cr1"), "POST", "/credentials/cr1/rotate", {}, None, 200,
     {"data": {"id": "cr1", "client_secret": "sek2"}}, Credential),
    ("revoke_credential", lambda m: m.revoke_credential("cr1"), "DELETE", "/credentials/cr1", {}, None, 200, DELETED,
     Deleted),
]


def _check(rsps, client, prefix, case):
    _name, call, method, path, query, body, status, answer, result_type = case
    rsps.add(method, API + prefix + path, json=answer, status=status)
    result = call(client)
    req = request(rsps)
    assert req.method == method
    assert path_of(req) == "/api/v1" + prefix + path
    assert query_of(req) == query
    assert body_of(req) == body
    assert isinstance(result, result_type)
    if method == "POST":
        assert uuid.UUID(req.headers["Idempotency-Key"]).version == 4
    else:
        assert "Idempotency-Key" not in req.headers
    return result


@pytest.mark.parametrize("case", CASES + ORG_CASES, ids=[c[0] for c in CASES + ORG_CASES])
def test_resource_method(mgmt, rsps, case):
    _check(rsps, mgmt, "", case)


@pytest.mark.parametrize("case", ORG_CASES, ids=[c[0] for c in ORG_CASES])
def test_managed_client_method_is_scoped_under_the_client(mgmt, rsps, case):
    managed = mgmt.for_managed_client("mc1")
    assert isinstance(managed, ManagedClient)
    _check(rsps, managed, "/clients/mc1", case)
    assert request(rsps).headers["Authorization"] == f"Bearer {KEY}"


class TestModels:
    def test_organization(self, mgmt, rsps):
        rsps.get(API + "/organization", json={"data": {"id": "o1", "name": "Acme", "slug": "acme", "domain": None,
                                                       "key": {"name": "ci", "scope": "read"}}})
        org = mgmt.get_organization()
        assert (org.id, org.name, org.slug, org.key.name, org.key.scope) == ("o1", "Acme", "acme", "ci", "read")

    def test_client_and_its_pre_assignments(self, mgmt, rsps):
        rsps.get(API + "/clients/c1", json={"data": {
            "id": "c1", "name": "Globex", "status": "pending", "invite_code": "INV-123", "managed": False,
            "claimed_at": None, "client_organization": None,
            "contacts": [{"id": "k1", "email": "a@globex.test", "first_name": "A", "last_name": "B"}],
            "pre_assignments": {
                "packages": [{"id": "pp1", "api_package_id": "p1", "status": "refused",
                              "refusal": {"code": "nothing_published_in_environment", "message": "m", "at": "t"}}],
                "grants": [{"id": "pg1", "target_application_id": "a1", "all_endpoints": True,
                            "status": "pending", "also_granted_by_api_package_ids": []}],
            },
            "future_field": 1,
        }})
        client = mgmt.get_client("c1")
        assert client.invite_code == "INV-123"
        assert "INV-123" not in repr(client)
        assert client.contacts[0].email == "a@globex.test"
        assert client.pre_assignments.packages[0].refusal.code == "nothing_published_in_environment"
        assert client.pre_assignments.grants[0].all_endpoints is True
        assert client.raw["future_field"] == 1

    def test_package_endpoint_added_carries_warnings(self, mgmt, rsps):
        rsps.post(API + "/api_packages/p1/endpoints", status=201, json={
            "data": {"id": "acc1", "application_id": "a1", "endpoint_id": "ep1", "all_endpoints": False,
                     "endpoint": {"id": "ep1", "path": "/orders", "action": "GET"}, "environment_id": "e1"},
            "warnings": [{"code": "assignment_derives_nothing", "message": "m", "client_organization_id": "co1",
                          "environment_id": "e2"}],
        })
        added = mgmt.add_package_endpoint("p1", application_id="a1", environment_id="e1", endpoint_id="ep1")
        assert added.endpoint.endpoint.path == "/orders"
        assert added.warnings[0].code == ErrorCode.ASSIGNMENT_DERIVES_NOTHING

    def test_deleted_grant_says_whether_a_package_still_grants_it(self, mgmt, rsps):
        rsps.delete(API + "/clients/c1/grants/g1",
                    json={"data": {"id": "g1", "deleted": True, "still_granted_by_package": True}})
        deleted = mgmt.delete_client_grant("c1", "g1")
        assert deleted.deleted is True and deleted.still_granted_by_package is True

    def test_credential_secret_is_returned_but_not_in_repr(self, mgmt, rsps):
        rsps.post(API + "/credentials", status=201, json={"data": {
            "id": "cr1", "client_id": "acme.abc", "client_secret": "s3cr3t-value", "secret_last_4": "alue",
            "application_environment_id": "ae1", "application_id": "a1",
            "environment": {"id": "e1", "name": "production"},
            "previous_secret": None, "expired_at": None, "created_at": "t", "updated_at": "t"}})
        credential = mgmt.create_credential("ae1")
        assert credential.client_secret == "s3cr3t-value"
        assert credential.environment.name == "production"
        assert "s3cr3t-value" not in repr(credential)
        assert "s3cr3t-value" not in str(credential)

    def test_rotated_credential_names_the_previous_secret(self, mgmt, rsps):
        rsps.post(API + "/credentials/cr1/rotate", json={"data": {
            "id": "cr1", "client_secret": "new", "previous_secret": {"last_4": "abcd", "expires_at": "t"}}})
        rotated = mgmt.rotate_credential("cr1")
        assert rotated.previous_secret.last_4 == "abcd"


# --- pagination -------------------------------------------------------------


class TestPagination:
    def test_one_page(self, mgmt, rsps):
        rsps.get(API + "/clients", json={"data": [{"id": "c1"}, {"id": "c2"}], "next_cursor": "cur"})
        page = mgmt.list_clients(limit=2)
        assert [c.id for c in page] == ["c1", "c2"]
        assert page.next_cursor == "cur" and page.has_more and len(page) == 2

    def test_iterates_every_page(self, mgmt, rsps):
        rsps.get(API + "/api_packages", json={"data": [{"id": "p1"}, {"id": "p2"}], "next_cursor": "cur2"})
        rsps.get(API + "/api_packages", json={"data": [{"id": "p3"}, {"id": "p4"}], "next_cursor": "cur3"})
        rsps.get(API + "/api_packages", json={"data": [{"id": "p5"}], "next_cursor": None})
        assert [p.id for p in mgmt.iter_api_packages(limit=2)] == ["p1", "p2", "p3", "p4", "p5"]
        assert [query_of(c.request) for c in rsps.calls] == [
            {"limit": "2"},
            {"limit": "2", "after": "cur2"},
            {"limit": "2", "after": "cur3"},
        ]

    def test_iterating_is_lazy(self, mgmt, rsps):
        rsps.get(API + "/environments", json={"data": [{"id": "e1"}], "next_cursor": "more"})
        iterator = mgmt.iter_environments()
        assert len(rsps.calls) == 0
        assert next(iterator).id == "e1"
        assert len(rsps.calls) == 1

    def test_iterates_a_nested_list_with_filters(self, mgmt, rsps):
        rsps.get(API + "/clients/c1/grants", json={"data": [{"id": "g1"}], "next_cursor": "n"})
        rsps.get(API + "/clients/c1/grants", json={"data": [{"id": "g2"}], "next_cursor": None})
        rsps.get(API + "/credentials", json={"data": [{"id": "cr1"}], "next_cursor": "n"})
        rsps.get(API + "/credentials", json={"data": [{"id": "cr2"}], "next_cursor": None})
        assert [g.id for g in mgmt.iter_client_grants("c1")] == ["g1", "g2"]
        assert [c.id for c in mgmt.iter_credentials(application_environment_id="ae1")] == ["cr1", "cr2"]
        assert query_of(rsps.calls[3].request) == {"application_environment_id": "ae1", "after": "n"}

    def test_iterators_of_every_list(self, mgmt, rsps):
        lists = {
            "/applications": mgmt.iter_applications,
            "/applications/a1/environments": lambda: mgmt.iter_application_environments("a1"),
            "/api_packages/p1/endpoints": lambda: mgmt.iter_package_endpoints("p1"),
            "/endpoints": lambda: mgmt.iter_endpoints(version="2"),
            "/clients": mgmt.iter_clients,
            "/clients/c1/packages": lambda: mgmt.iter_client_packages("c1"),
        }
        for path in lists:
            rsps.get(API + path, json={"data": [{"id": "x"}], "next_cursor": None})
        for path, make in lists.items():
            assert [item.id for item in make()] == ["x"], path

    def test_the_managed_client_iterates_its_own_lists(self, mgmt, rsps):
        rsps.get(API + "/clients/mc1/applications", json={"data": [{"id": "a1"}], "next_cursor": "n"})
        rsps.get(API + "/clients/mc1/applications", json={"data": [{"id": "a2"}], "next_cursor": None})
        assert [a.id for a in mgmt.for_managed_client("mc1").iter_applications()] == ["a1", "a2"]

    @pytest.mark.parametrize("limit", [0, 101, -1, 2.0, "10", True])
    def test_refuses_a_bad_limit_without_a_request(self, mgmt, limit):
        with pytest.raises(ValueError):
            mgmt.list_clients(limit=limit)

    @pytest.mark.parametrize("limit", [0, 101, "10"])
    def test_an_iterator_refuses_a_bad_limit_when_made(self, mgmt, limit):
        with pytest.raises(ValueError):
            mgmt.iter_clients(limit=limit)
        with pytest.raises(ValueError):
            mgmt.iter_client_grants("c1", limit=limit)

    def test_a_repeated_cursor_stops_paging_loudly(self, mgmt, rsps):
        rsps.get(API + "/clients", json={"data": [{"id": "c1"}], "next_cursor": "same"})
        rsps.get(API + "/clients", json={"data": [{"id": "c2"}], "next_cursor": "same"})
        seen = []
        with pytest.raises(InvalidResponseError):
            for client in mgmt.iter_clients():
                seen.append(client.id)
        assert seen == ["c1", "c2"] and len(rsps.calls) == 2

    @pytest.mark.parametrize("limit", [1, 100])
    def test_accepts_the_limit_bounds(self, mgmt, rsps, limit):
        rsps.get(API + "/clients", json={"data": [], "next_cursor": None})
        mgmt.list_clients(limit=limit)
        assert query_of(request(rsps)) == {"limit": str(limit)}


# --- idempotency and retries ------------------------------------------------


class TestIdempotency:
    def test_each_post_gets_its_own_generated_key(self, mgmt, rsps):
        rsps.post(API + "/environments", json=ONE, status=201)
        rsps.post(API + "/environments", json=ONE, status=201)
        mgmt.create_environment("A", "a.test")
        mgmt.create_environment("B", "b.test")
        keys = [c.request.headers["Idempotency-Key"] for c in rsps.calls]
        assert keys[0] != keys[1]
        assert all(uuid.UUID(k).version == 4 for k in keys)

    def test_the_callers_key_is_sent(self, mgmt, rsps):
        rsps.post(API + "/clients/c1/packages", json=ONE, status=201)
        mgmt.assign_package("c1", api_package_id="p1", environment_id="e1", idempotency_key="assign-c1-p1")
        assert request(rsps).headers["Idempotency-Key"] == "assign-c1-p1"

    @pytest.mark.parametrize("key", ["", "   ", "x" * 256, "é" * 128])
    def test_refuses_a_bad_key_without_a_request(self, mgmt, key):
        with pytest.raises(ValueError):
            mgmt.create_api_package("Gold", idempotency_key=key)

    def test_the_same_generated_key_is_reused_on_retry(self, mgmt, rsps, sleeps):
        rsps.post(API + "/applications", json=error_body("audit_unavailable"), status=503)
        rsps.post(API + "/applications", json=error_body("internal_server_error"), status=500)
        rsps.post(API + "/applications", json=ONE, status=201)
        mgmt.create_application("Orders", {"e1": "https://orders.test"})
        keys = {c.request.headers["Idempotency-Key"] for c in rsps.calls}
        assert len(rsps.calls) == 3 and len(keys) == 1
        assert sleeps == [0.5, 1.0]

    def test_the_callers_key_is_reused_on_retry_after_429(self, mgmt, rsps, sleeps):
        rsps.post(API + "/credentials/cr1/rotate", json=error_body("rate_limited"), status=429,
                  headers={"Retry-After": "3"})
        rsps.post(API + "/credentials/cr1/rotate", json={"data": {"id": "cr1", "client_secret": "s"}})
        mgmt.rotate_credential("cr1", idempotency_key="rotate-1")
        assert [c.request.headers["Idempotency-Key"] for c in rsps.calls] == ["rotate-1", "rotate-1"]
        assert sleeps == [3.0]

    def test_in_progress_is_retried_with_the_same_key(self, mgmt, rsps, sleeps):
        rsps.post(API + "/clients", json=error_body("idempotency_request_in_progress"), status=409)
        rsps.post(API + "/clients", json={"data": {"id": "c1"}}, status=201)
        mgmt.create_client("Globex")
        assert len({c.request.headers["Idempotency-Key"] for c in rsps.calls}) == 1
        assert sleeps == [0.5]

    def test_a_lost_connection_on_post_is_retried_with_the_same_key(self, mgmt, rsps):
        rsps.post(API + "/api_packages", body=requests.ConnectionError("reset"))
        rsps.post(API + "/api_packages", json=ONE, status=201)
        mgmt.create_api_package("Gold")
        assert len({c.request.headers["Idempotency-Key"] for c in rsps.calls}) == 1

    def test_replay_unavailable_is_not_retried_and_says_to_read_the_resource(self, mgmt, rsps, sleeps):
        rsps.post(API + "/credentials", status=409, headers={"Location": "/api/v1/credentials/cr1"},
                  json=error_body("idempotency_replay_unavailable", "can't be replayed."))
        with pytest.raises(IdempotencyReplayUnavailableError) as info:
            mgmt.create_credential("ae1", idempotency_key="k1")
        error = info.value
        assert isinstance(error, ConflictError)
        assert error.code == "idempotency_replay_unavailable" and error.status == 409
        assert error.location == "/api/v1/credentials/cr1"
        assert "read or list the resource" in error.message
        assert len(rsps.calls) == 1 and sleeps == []


class TestRetries:
    def test_429_waits_retry_after_then_succeeds(self, mgmt, rsps, sleeps):
        rsps.get(API + "/clients", json=error_body("rate_limited"), status=429, headers={"Retry-After": "7"})
        rsps.get(API + "/clients", json={"data": [{"id": "c1"}], "next_cursor": None})
        assert [c.id for c in mgmt.list_clients()] == ["c1"]
        assert sleeps == [7.0]

    def test_429_is_retried_a_bounded_number_of_times(self, mgmt, rsps, sleeps):
        for _ in range(3):
            rsps.get(API + "/organization", json=error_body("rate_limited", "slow down"), status=429,
                     headers={"Retry-After": "2"})
        with pytest.raises(RateLimitedError) as info:
            mgmt.get_organization()
        assert info.value.retry_after == 2.0 and info.value.status == 429
        assert sleeps == [2.0, 2.0]
        assert len(rsps.calls) == 3

    def test_retries_are_configurable(self, rsps, sleeps):
        client = ManagementClient(KEY, base_url=BASE, sleep=sleeps.append, max_retries=4)
        for _ in range(4):
            rsps.get(API + "/organization", json=error_body("rate_limited"), status=429, headers={"Retry-After": "1"})
        rsps.get(API + "/organization", json={"data": {"id": "o1"}})
        assert client.get_organization().id == "o1"
        assert sleeps == [1.0] * 4

    def test_retries_can_be_turned_off(self, rsps, sleeps):
        client = ManagementClient(KEY, base_url=BASE, sleep=sleeps.append, max_retries=0)
        rsps.get(API + "/organization", json=error_body("rate_limited"), status=429, headers={"Retry-After": "1"})
        with pytest.raises(RateLimitedError):
            client.get_organization()
        assert sleeps == [] and client.max_retries == 0

    def test_a_retry_after_longer_than_max_retry_wait_is_not_waited_out(self, rsps, sleeps):
        client = ManagementClient(KEY, base_url=BASE, sleep=sleeps.append, max_retry_wait=5)
        rsps.get(API + "/organization", json=error_body("rate_limited"), status=429, headers={"Retry-After": "30"})
        with pytest.raises(RateLimitedError) as info:
            client.get_organization()
        assert info.value.retry_after == 30.0 and sleeps == []

    def test_retry_after_as_an_http_date(self, mgmt, rsps, sleeps):
        rsps.get(API + "/organization", json=error_body("rate_limited"), status=429,
                 headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
        rsps.get(API + "/organization", json={"data": {"id": "o1"}})
        mgmt.get_organization()
        assert sleeps == [0.0]

    def test_429_without_retry_after_backs_off(self, mgmt, rsps, sleeps):
        rsps.get(API + "/organization", json=error_body("rate_limited"), status=429)
        rsps.get(API + "/organization", json={"data": {"id": "o1"}})
        mgmt.get_organization()
        assert sleeps == [0.5]

    def test_patch_is_retried_after_429(self, mgmt, rsps, sleeps):
        rsps.patch(API + "/applications/a1", json=error_body("rate_limited"), status=429, headers={"Retry-After": "1"})
        rsps.patch(API + "/applications/a1", json=ONE)
        mgmt.update_application("a1", public=True)
        assert sleeps == [1.0]

    @pytest.mark.parametrize("status,code", [(500, "internal_server_error"), (503, "intake_unavailable")])
    def test_patch_is_never_retried_after_a_5xx(self, mgmt, rsps, sleeps, status, code):
        rsps.patch(API + "/environments/e1", json=error_body(code), status=status)
        with pytest.raises(ServerError):
            mgmt.update_environment("e1", name="x")
        assert len(rsps.calls) == 1 and sleeps == []

    def test_patch_is_not_retried_after_a_lost_connection(self, mgmt, rsps, sleeps):
        rsps.patch(API + "/api_packages/p1", body=requests.ConnectionError("reset"))
        with pytest.raises(ManagementConnectionError):
            mgmt.update_api_package("p1", name="x")
        assert sleeps == []

    @pytest.mark.parametrize("method,call,path", [
        ("GET", lambda m: m.get_organization(), "/organization"),
        ("DELETE", lambda m: m.revoke_credential("cr1"), "/credentials/cr1"),
    ])
    def test_get_and_delete_are_retried_after_a_5xx(self, mgmt, rsps, sleeps, method, call, path):
        rsps.add(method, API + path, json=error_body("intake_unavailable"), status=503)
        rsps.add(method, API + path, body="<html>bad gateway</html>", status=502, content_type="text/html")
        rsps.add(method, API + path, json={"data": {"id": "x", "deleted": True}})
        call(mgmt)
        assert len(rsps.calls) == 3 and sleeps == [0.5, 1.0]

    def test_a_lost_connection_is_retried_then_raised(self, mgmt, rsps, sleeps):
        for _ in range(3):
            rsps.get(API + "/organization", body=requests.ConnectTimeout("timed out"))
        with pytest.raises(ManagementConnectionError) as info:
            mgmt.get_organization()
        assert info.value.code == ErrorCode.CONNECTION_ERROR and info.value.status is None
        assert "ConnectTimeout" in info.value.message
        assert sleeps == [0.5, 1.0]

    def test_a_4xx_is_not_retried(self, mgmt, rsps, sleeps):
        rsps.get(API + "/clients/c1", json=error_body("not_found"), status=404)
        with pytest.raises(NotFoundError):
            mgmt.get_client("c1")
        assert sleeps == []


# --- error mapping ------------------------------------------------------------


class TestErrors:
    def test_not_found(self, mgmt, rsps):
        rsps.get(API + "/api_packages/p1", json=error_body("not_found", "Not found."), status=404)
        with pytest.raises(NotFoundError) as info:
            mgmt.get_api_package("p1")
        error = info.value
        assert isinstance(error, ManagementApiError)
        assert (error.code, error.message, error.status, error.details) == ("not_found", "Not found.", 404, None)
        assert error.code == ErrorCode.NOT_FOUND
        assert (error.method, error.path) == ("GET", "/api_packages/p1")
        assert str(error) == "404 not_found: Not found. (GET /api_packages/p1)"

    def test_validation_failed_carries_details(self, mgmt, rsps):
        details = {"name": ["can't be blank"]}
        rsps.post(API + "/environments", json=error_body("validation_failed", "The request has invalid fields.",
                                                          details), status=422)
        with pytest.raises(ValidationFailedError) as info:
            mgmt.create_environment("", "x.test")
        assert info.value.details == details and info.value.status == 422

    def test_plan_limit(self, mgmt, rsps):
        rsps.post(API + "/clients", json=error_body("plan_limit"), status=402)
        with pytest.raises(PlanLimitError) as info:
            mgmt.create_client("Globex")
        assert info.value.code == "plan_limit" and info.value.status == 402

    @pytest.mark.parametrize("status,code,cls", [
        (401, "missing_key", AuthenticationError),
        (401, "invalid_key", AuthenticationError),
        (401, "runtime_credential_refused", AuthenticationError),
        (403, "insufficient_scope", InsufficientScopeError),
        (400, "invalid_pagination", BadRequestError),
        (409, "intake_credential", ConflictError),
        (409, "grant_revoked_concurrently", ConflictError),
        (422, "has_dependents", RequestRefusedError),
        (422, "delete_refused", RequestRefusedError),
        (422, "nothing_published_in_environment", RequestRefusedError),
        (422, "return_to_not_registered", RequestRefusedError),
        (503, "audit_unavailable", ServiceUnavailableError),
        (500, "internal_server_error", ServerError),
    ])
    def test_each_code_maps_to_its_class(self, rsps, status, code, cls):
        client = ManagementClient(KEY, base_url=BASE, max_retries=0)
        rsps.delete(API + "/applications/a1", json=error_body(code), status=status)
        with pytest.raises(cls) as info:
            client.delete_application("a1")
        assert info.value.code == code and info.value.status == status

    def test_claim_invite_without_return_to_leaves_it_out_of_the_body(self, mgmt, rsps):
        rsps.post(API + "/clients/c1/claim_invites", json={"data": {"client_id": "c1"}}, status=201)
        mgmt.send_claim_invite("c1", "owner@initech.test")
        assert "return_to" not in body_of(request(rsps))

    def test_an_unregistered_return_to_is_refused(self, mgmt, rsps):
        rsps.post(API + "/clients/c1/claim_invites", json=error_body("return_to_not_registered"), status=422)
        with pytest.raises(RequestRefusedError) as info:
            mgmt.send_claim_invite("c1", "owner@initech.test", return_to="https://evil.example.test/")
        assert info.value.code == ErrorCode.RETURN_TO_NOT_REGISTERED and info.value.status == 422
        assert body_of(request(rsps))["return_to"] == "https://evil.example.test/"

    def test_an_unknown_code_still_surfaces(self, mgmt, rsps):
        rsps.post(API + "/clients/c1/grants", json=error_body("brand_new_refusal", "New."), status=422)
        with pytest.raises(RequestRefusedError) as info:
            mgmt.create_client_grant("c1", target_application_id="a1", environment_id="e1")
        assert info.value.code == "brand_new_refusal" and info.value.message == "New."

    def test_an_unknown_status_is_the_base_error(self, mgmt, rsps):
        rsps.get(API + "/organization", json=error_body("teapot"), status=418)
        with pytest.raises(ManagementApiError) as info:
            mgmt.get_organization()
        assert type(info.value) is ManagementApiError and info.value.code == "teapot"

    def test_a_non_json_error_body(self, rsps):
        client = ManagementClient(KEY, base_url=BASE, max_retries=0)
        rsps.get(API + "/organization", body="<html>Bad Gateway</html>", status=502, content_type="text/html")
        with pytest.raises(ServerError) as info:
            client.get_organization()
        assert info.value.code == "http_502" and info.value.status == 502
        assert "text/html" in info.value.message

    def test_a_json_error_body_of_another_shape(self, mgmt, rsps):
        rsps.get(API + "/organization", json={"errors": {"detail": "Not Found"}}, status=404)
        with pytest.raises(NotFoundError) as info:
            mgmt.get_organization()
        assert info.value.code == "http_404"

    def test_a_success_body_that_is_not_json(self, mgmt, rsps):
        rsps.get(API + "/organization", body="ok", status=200, content_type="text/plain")
        with pytest.raises(InvalidResponseError):
            mgmt.get_organization()

    def test_a_success_body_without_data(self, mgmt, rsps):
        rsps.get(API + "/clients", json={"items": []})
        with pytest.raises(InvalidResponseError):
            mgmt.list_clients()

    def test_any_other_request_error_is_named_by_class_only(self, mgmt, rsps, caplog):
        caplog.set_level(logging.DEBUG)
        rsps.get(API + "/organization", body=requests.exceptions.InvalidHeader(f"bad header value: 'Bearer {KEY}'"))
        with pytest.raises(ManagementApiError) as info:
            mgmt.get_organization()
        error = info.value
        assert error.code == ErrorCode.REQUEST_ERROR and "InvalidHeader" in error.message
        assert error.__cause__ is None and error.__suppress_context__
        for text in (str(error), repr(error), repr(vars(error)), caplog.text):
            assert KEY not in text
        assert len(rsps.calls) == 1

    def test_errors_never_carry_the_key(self, mgmt, rsps):
        rsps.get(API + "/organization", json=error_body("invalid_key", "The key is invalid."), status=401)
        with pytest.raises(AuthenticationError) as info:
            mgmt.get_organization()
        for text in (str(info.value), repr(info.value), repr(vars(info.value))):
            assert KEY not in text


# The codes app_portal's ErrorCodes module documents (app_portal#432).
DOCUMENTED_CODES = {
    "missing_key", "invalid_key", "runtime_credential_refused", "insufficient_scope", "audit_unavailable",
    "rate_limited", "plan_limit", "validation_failed", "not_found", "invalid_pagination", "invalid_filter",
    "invalid_idempotency_key", "idempotency_key_reused", "idempotency_request_in_progress",
    "idempotency_replay_unavailable", "bad_request", "internal_server_error", "has_dependents", "protected",
    "invalid_environment_base_urls", "api_package_assigned", "intake_sync_failed", "delete_refused",
    "intake_credential", "intake_rejected", "intake_unavailable", "invalid_contacts", "invalid_packages",
    "invalid_grants", "invalid_managed", "client_not_accepted", "client_accepted", "client_not_managed",
    "already_a_member", "return_to_not_registered", "managed_client_has_credentials", "api_package_not_found", "environment_not_found",
    "already_assigned", "nothing_published_in_environment", "application_not_found", "endpoint_not_found",
    "environment_not_in_application", "already_granted", "grant_revoked_concurrently",
}


def test_error_code_lists_exactly_the_documented_codes():
    sdk_own = {"connection_error", "request_error", "invalid_response", "assignment_derives_nothing"}
    assert {c.value for c in ErrorCode} - sdk_own == DOCUMENTED_CODES


class TestLogging:
    def test_neither_the_key_nor_a_secret_is_logged(self, mgmt, rsps, caplog):
        caplog.set_level(logging.DEBUG)
        rsps.post(API + "/credentials", json=error_body("intake_unavailable"), status=503)
        rsps.post(API + "/credentials", json={"data": {"id": "cr1", "client_secret": "s3cr3t-value"}}, status=201)
        rsps.get(API + "/organization", body=requests.ConnectionError(f"Bearer {KEY}"))
        rsps.get(API + "/organization", json={"data": {"id": "o1"}})
        mgmt.create_credential("ae1")
        mgmt.get_organization()
        assert caplog.records, "the retries are logged"
        for record in caplog.records:
            text = record.getMessage()
            assert KEY not in text
            assert "s3cr3t-value" not in text
