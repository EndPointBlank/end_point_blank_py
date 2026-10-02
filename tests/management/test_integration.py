"""
The management client against a real app_portal: skipped unless
``EPB_MGMT_BASE_URL`` and ``EPB_MGMT_KEY`` (a write key) are set, e.g.::

    EPB_MGMT_BASE_URL=http://localhost:4000 EPB_MGMT_KEY=epb_mk_... ./test.sh tests/management

It runs the story's flow on the key's organization and removes everything it
created. Refusals the organization's state can cause (nothing deployed to
publish, a plan's client limit) are asserted by code rather than failed.
"""
import os
import uuid

import pytest

from end_point_blank.management import (
    ManagementApiError,
    ManagementClient,
    NotFoundError,
    PlanLimitError,
)

BASE_URL = os.environ.get("EPB_MGMT_BASE_URL")
KEY = os.environ.get("EPB_MGMT_KEY")

pytestmark = pytest.mark.skipif(
    not (BASE_URL and KEY), reason="set EPB_MGMT_BASE_URL and EPB_MGMT_KEY to run against an app_portal"
)

# What an organization with nothing deployed, or a fresh package, is refused.
EXPECTED_ASSIGN_REFUSALS = {"nothing_published_in_environment", "already_assigned", "client_not_accepted"}


@pytest.fixture
def mgmt():
    return ManagementClient(KEY, base_url=BASE_URL)


@pytest.fixture
def cleanup():
    """Undo steps, run last-first even when the test fails. A step that finds
    its resource already gone (404) is fine."""
    steps = []
    yield steps.append
    errors = []
    for step in reversed(steps):
        try:
            step()
        except NotFoundError:
            pass
        except ManagementApiError as exc:  # pragma: no cover - reported, not hidden
            errors.append(exc)
    assert not errors, f"cleanup failed: {errors}"


def test_the_story_flow(mgmt, cleanup):
    tag = "sdk-py-it-" + uuid.uuid4().hex[:8]

    org = mgmt.get_organization()
    assert org.id and org.key.scope == "write"

    # An application in two new environments: one placed on create, one after.
    env_a = mgmt.create_environment(f"{tag}-a", f"{tag}-a.example.test")
    cleanup(lambda: mgmt.delete_environment(env_a.id))
    env_b = mgmt.create_environment(f"{tag}-b", f"{tag}-b.example.test")
    cleanup(lambda: mgmt.delete_environment(env_b.id))
    assert mgmt.get_environment(env_a.id).name == f"{tag}-a"

    app = mgmt.create_application(tag, {env_a.id: f"https://{tag}-a.example.test"})
    cleanup(lambda: mgmt.delete_application(app.id))
    app_env_b = mgmt.create_application_environment(app.id, env_b.id, f"https://{tag}-b.example.test")
    cleanup(lambda: mgmt.delete_application_environment(app.id, app_env_b.id))
    assert {ae.environment_id for ae in mgmt.iter_application_environments(app.id)} == {env_a.id, env_b.id}
    assert mgmt.update_application(app.id, name=f"{tag}-renamed").name == f"{tag}-renamed"

    # An API package, with a deployed endpoint when the organization has one.
    package = mgmt.create_api_package(tag)
    cleanup(lambda: mgmt.delete_api_package(package.id))
    package_env_id = env_a.id
    deployed = mgmt.list_endpoints(limit=1).items
    if deployed:
        endpoint = deployed[0]
        placements = mgmt.list_application_environments(endpoint.application_id, limit=1).items
        if placements:
            package_env_id = placements[0].environment_id
            added = mgmt.add_package_endpoint(
                package.id,
                application_id=endpoint.application_id,
                endpoint_id=endpoint.id,
                environment_id=package_env_id,
            )
            cleanup(lambda: mgmt.remove_package_endpoint(package.id, added.endpoint.id))
            assert [e.id for e in mgmt.iter_package_endpoints(package.id)] == [added.endpoint.id]

    # Invite a client and assign the package if the organization's state allows.
    try:
        client = mgmt.create_client(
            f"{tag} client",
            contacts=[{"email": f"{tag}@example.test", "first_name": "Sdk", "last_name": "Test"}],
        )
    except PlanLimitError as exc:
        assert exc.code == "plan_limit"
        client = None
    if client is not None:
        cleanup(lambda: mgmt.delete_client(client.id))
        assert client.status == "pending"
        assert any(c.id == client.id for c in mgmt.iter_clients())
        try:
            assignment = mgmt.assign_package(client.id, api_package_id=package.id, environment_id=package_env_id)
        except ManagementApiError as exc:
            assert exc.code in EXPECTED_ASSIGN_REFUSALS, exc
        else:
            cleanup(lambda: mgmt.unassign_package(client.id, assignment.id))
            assert assignment.status in ("pending", "active")
            assert [a.id for a in mgmt.iter_client_packages(client.id)] == [assignment.id]

    # A credential: created, rotated, revoked.
    credential = mgmt.create_credential(app_env_b.id)
    cleanup(lambda: mgmt.revoke_credential(credential.id))
    assert credential.client_secret
    assert mgmt.get_credential(credential.id).client_secret is None
    rotated = mgmt.rotate_credential(credential.id)
    assert rotated.client_secret and rotated.client_secret != credential.client_secret
    assert rotated.previous_secret is not None
    assert mgmt.revoke_credential(credential.id).deleted is True

    # A managed client, set up under /clients/<id>/..., then removed.
    try:
        managed = mgmt.create_managed_client(f"{tag} managed")
    except PlanLimitError as exc:
        assert exc.code == "plan_limit"
        return
    cleanup(lambda: mgmt.delete_client(managed.id))
    assert managed.managed is True and managed.status == "accepted"

    scoped = mgmt.for_managed_client(managed.id)
    m_env = scoped.create_environment(f"{tag}-m", f"{tag}-m.example.test")
    cleanup(lambda: scoped.delete_environment(m_env.id))
    m_app = scoped.create_application(f"{tag}-m", {m_env.id: f"https://{tag}-m.example.test"})
    cleanup(lambda: scoped.delete_application(m_app.id))
    m_app_env = scoped.list_application_environments(m_app.id).items[0]
    m_credential = scoped.create_credential(m_app_env.id)
    assert m_credential.client_secret
    assert [c.id for c in scoped.iter_credentials(application_environment_id=m_app_env.id)] == [m_credential.id]
    assert scoped.revoke_credential(m_credential.id).deleted is True
    # Its own organization's resources are not the provider's.
    with pytest.raises(NotFoundError):
        mgmt.get_application(m_app.id)
