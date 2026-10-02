"""
Typed views of the management API's JSON answers.

Each model is a frozen dataclass built by ``from_dict`` from one ``data``
object. Fields the API documents are attributes; timestamps are kept as the
ISO 8601 strings the API sends. Every model also keeps the object it was built
from as ``raw`` (left out of ``repr`` and equality), so a field the API adds
later is readable before this SDK names it.

Nothing here logs. :class:`Credential` leaves ``client_secret`` out of its
``repr`` so printing one does not print the secret.

This module imports nothing from the package.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Generic, Iterator, List, Optional, TypeVar

T = TypeVar("T")

_RAW = dict(default_factory=dict, repr=False, compare=False)


def _list(values: Any, model: Any) -> List[Any]:
    return [model.from_dict(v) for v in (values or [])]


def _opt(value: Any, model: Any) -> Any:
    return model.from_dict(value) if isinstance(value, dict) else None


@dataclass(frozen=True)
class Page(Generic[T]):
    """
    One page of a list: ``items`` and the ``next_cursor`` to pass as
    ``after=`` for the next page (``None`` on the last page). Iterating a page
    iterates its items.
    """

    items: List[T]
    next_cursor: Optional[str] = None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)


@dataclass(frozen=True)
class KeyInfo:
    """The management key a request was made with (its name and scope only)."""

    name: Optional[str]
    scope: Optional[str]
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KeyInfo":
        return cls(name=d.get("name"), scope=d.get("scope"), raw=d)


@dataclass(frozen=True)
class Organization:
    """``GET /organization``: the organization the key belongs to."""

    id: str
    name: Optional[str]
    domain: Optional[str]
    slug: Optional[str]
    key: Optional[KeyInfo]
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Organization":
        return cls(
            id=d.get("id"),
            name=d.get("name"),
            domain=d.get("domain"),
            slug=d.get("slug"),
            key=_opt(d.get("key"), KeyInfo),
            raw=d,
        )


@dataclass(frozen=True)
class Deleted:
    """
    The answer to a ``DELETE``: the id and ``deleted: true``.
    ``still_granted_by_package`` is set only for a client's direct grant, and
    ``warnings`` only for a package endpoint.
    """

    id: str
    deleted: bool
    still_granted_by_package: Optional[bool] = None
    warnings: List["ApiWarning"] = field(default_factory=list)
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any], warnings: Any = None) -> "Deleted":
        return cls(
            id=d.get("id"),
            deleted=bool(d.get("deleted")),
            still_granted_by_package=d.get("still_granted_by_package"),
            warnings=_list(warnings, ApiWarning),
            raw=d,
        )


@dataclass(frozen=True)
class ApiWarning:
    """A warning beside a 2xx answer, e.g. ``assignment_derives_nothing``."""

    code: str
    message: Optional[str]
    client_organization_id: Optional[str] = None
    environment_id: Optional[str] = None
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ApiWarning":
        return cls(
            code=d.get("code"),
            message=d.get("message"),
            client_organization_id=d.get("client_organization_id"),
            environment_id=d.get("environment_id"),
            raw=d,
        )


# --- API packages and endpoints ---------------------------------------------


@dataclass(frozen=True)
class ApiPackage:
    id: str
    name: Optional[str]
    organization_id: Optional[str]
    inserted_at: Optional[str] = None
    updated_at: Optional[str] = None
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ApiPackage":
        return cls(
            id=d.get("id"),
            name=d.get("name"),
            organization_id=d.get("organization_id"),
            inserted_at=d.get("inserted_at"),
            updated_at=d.get("updated_at"),
            raw=d,
        )


@dataclass(frozen=True)
class EndpointRef:
    """The endpoint a package entry names: id, path and action."""

    id: str
    path: Optional[str]
    action: Optional[str]
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EndpointRef":
        return cls(id=d.get("id"), path=d.get("path"), action=d.get("action"), raw=d)


@dataclass(frozen=True)
class PackageEndpoint:
    """
    One entry of what an API package publishes: an endpoint of an application
    (or, with ``all_endpoints``, every endpoint of it) in one environment. Its
    ``id`` is the ``access_id`` that removes it.
    """

    id: str
    api_package_id: Optional[str]
    application_id: Optional[str]
    endpoint_id: Optional[str]
    all_endpoints: bool
    endpoint: Optional[EndpointRef]
    environment_id: Optional[str]
    inserted_at: Optional[str] = None
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PackageEndpoint":
        return cls(
            id=d.get("id"),
            api_package_id=d.get("api_package_id"),
            application_id=d.get("application_id"),
            endpoint_id=d.get("endpoint_id"),
            all_endpoints=bool(d.get("all_endpoints")),
            endpoint=_opt(d.get("endpoint"), EndpointRef),
            environment_id=d.get("environment_id"),
            inserted_at=d.get("inserted_at"),
            raw=d,
        )


@dataclass(frozen=True)
class PackageEndpointAdded:
    """The answer to adding an endpoint to a package: the new entry and the
    client assignments of the package that now derive no grant."""

    endpoint: PackageEndpoint
    warnings: List[ApiWarning] = field(default_factory=list)


@dataclass(frozen=True)
class Endpoint:
    """An endpoint of one of the organization's applications (``GET /endpoints``)."""

    id: str
    application_id: Optional[str]
    path: Optional[str]
    action: Optional[str]
    public: Optional[bool] = None
    inserted_at: Optional[str] = None
    updated_at: Optional[str] = None
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Endpoint":
        return cls(
            id=d.get("id"),
            application_id=d.get("application_id"),
            path=d.get("path"),
            action=d.get("action"),
            public=d.get("public"),
            inserted_at=d.get("inserted_at"),
            updated_at=d.get("updated_at"),
            raw=d,
        )


# --- Clients, assignments and grants -----------------------------------------


@dataclass(frozen=True)
class Contact:
    id: Optional[str]
    email: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    title: Optional[str] = None
    phone_number: Optional[str] = None
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Contact":
        return cls(
            id=d.get("id"),
            email=d.get("email"),
            first_name=d.get("first_name"),
            last_name=d.get("last_name"),
            title=d.get("title"),
            phone_number=d.get("phone_number"),
            raw=d,
        )


@dataclass(frozen=True)
class OrganizationRef:
    id: str
    name: Optional[str]
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OrganizationRef":
        return cls(id=d.get("id"), name=d.get("name"), raw=d)


@dataclass(frozen=True)
class Refusal:
    """Why a pre-assigned package or grant could not be applied on accept."""

    code: Optional[str]
    message: Optional[str]
    at: Optional[str]
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Refusal":
        return cls(code=d.get("code"), message=d.get("message"), at=d.get("at"), raw=d)


@dataclass(frozen=True)
class PackageAssignment:
    """
    An API package a client holds (``status`` ``"active"``), or one set up for
    a client that has not accepted yet (``"pending"``) or could not be applied
    when it did (``"refused"``, see ``refusal``).
    """

    id: str
    api_package_id: Optional[str]
    api_package_name: Optional[str]
    environment_id: Optional[str]
    status: Optional[str]
    refusal: Optional[Refusal] = None
    inserted_at: Optional[str] = None
    updated_at: Optional[str] = None
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PackageAssignment":
        return cls(
            id=d.get("id"),
            api_package_id=d.get("api_package_id"),
            api_package_name=d.get("api_package_name"),
            environment_id=d.get("environment_id"),
            status=d.get("status"),
            refusal=_opt(d.get("refusal"), Refusal),
            inserted_at=d.get("inserted_at"),
            updated_at=d.get("updated_at"),
            raw=d,
        )


@dataclass(frozen=True)
class Grant:
    """
    A grant a client holds directly. ``target_endpoint_id`` ``None``
    (``all_endpoints``) covers every endpoint of the target application.
    ``status`` and ``refusal`` are as for :class:`PackageAssignment`.
    """

    id: str
    client_organization_id: Optional[str]
    target_application_id: Optional[str]
    target_endpoint_id: Optional[str]
    all_endpoints: bool
    environment_id: Optional[str]
    also_granted_by_api_package_ids: List[str] = field(default_factory=list)
    synced_at: Optional[str] = None
    status: Optional[str] = None
    refusal: Optional[Refusal] = None
    inserted_at: Optional[str] = None
    updated_at: Optional[str] = None
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Grant":
        return cls(
            id=d.get("id"),
            client_organization_id=d.get("client_organization_id"),
            target_application_id=d.get("target_application_id"),
            target_endpoint_id=d.get("target_endpoint_id"),
            all_endpoints=bool(d.get("all_endpoints")),
            environment_id=d.get("environment_id"),
            also_granted_by_api_package_ids=list(d.get("also_granted_by_api_package_ids") or []),
            synced_at=d.get("synced_at"),
            status=d.get("status"),
            refusal=_opt(d.get("refusal"), Refusal),
            inserted_at=d.get("inserted_at"),
            updated_at=d.get("updated_at"),
            raw=d,
        )


@dataclass(frozen=True)
class PreAssignments:
    """What a single client was set up to get before it accepted and is not an
    assignment or a grant yet (pending, or refused on accept)."""

    packages: List[PackageAssignment] = field(default_factory=list)
    grants: List[Grant] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PreAssignments":
        return cls(
            packages=_list(d.get("packages"), PackageAssignment),
            grants=_list(d.get("grants"), Grant),
        )


@dataclass(frozen=True)
class Client:
    """
    A client of the key's organization: a client invite, ``"pending"`` or
    ``"accepted"``. ``invite_code`` is set only while pending and only for a
    write key; it is a bearer secret, so it is left out of ``repr``.
    ``contacts`` and ``pre_assignments`` are set on a single client only.
    """

    id: str
    name: Optional[str]
    status: Optional[str]
    accepted_at: Optional[str] = None
    managed: bool = False
    claimed_at: Optional[str] = None
    client_organization: Optional[OrganizationRef] = None
    invite_code: Optional[str] = field(default=None, repr=False)
    contacts: Optional[List[Contact]] = None
    pre_assignments: Optional[PreAssignments] = None
    inserted_at: Optional[str] = None
    updated_at: Optional[str] = None
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Client":
        contacts = d.get("contacts")
        return cls(
            id=d.get("id"),
            name=d.get("name"),
            status=d.get("status"),
            accepted_at=d.get("accepted_at"),
            managed=bool(d.get("managed")),
            claimed_at=d.get("claimed_at"),
            client_organization=_opt(d.get("client_organization"), OrganizationRef),
            invite_code=d.get("invite_code"),
            contacts=_list(contacts, Contact) if contacts is not None else None,
            pre_assignments=_opt(d.get("pre_assignments"), PreAssignments),
            inserted_at=d.get("inserted_at"),
            updated_at=d.get("updated_at"),
            raw=d,
        )


@dataclass(frozen=True)
class ClaimInvite:
    """A claim invite sent to a managed client's customer."""

    client_id: str
    email: Optional[str]
    sent_at: Optional[str] = None
    expires_at: Optional[str] = None
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ClaimInvite":
        return cls(
            client_id=d.get("client_id"),
            email=d.get("email"),
            sent_at=d.get("sent_at"),
            expires_at=d.get("expires_at"),
            raw=d,
        )


# --- Applications, environments and credentials -------------------------------


@dataclass(frozen=True)
class Application:
    id: str
    name: Optional[str]
    public: Optional[bool] = None
    organization_group_id: Optional[str] = None
    synced_at: Optional[str] = None
    inserted_at: Optional[str] = None
    updated_at: Optional[str] = None
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Application":
        return cls(
            id=d.get("id"),
            name=d.get("name"),
            public=d.get("public"),
            organization_group_id=d.get("organization_group_id"),
            synced_at=d.get("synced_at"),
            inserted_at=d.get("inserted_at"),
            updated_at=d.get("updated_at"),
            raw=d,
        )


@dataclass(frozen=True)
class Environment:
    """An environment. ``production`` is the system-managed one, which can't
    be changed or deleted."""

    id: str
    name: Optional[str]
    domain: Optional[str] = None
    is_default: Optional[bool] = None
    production: Optional[bool] = None
    synced_at: Optional[str] = None
    inserted_at: Optional[str] = None
    updated_at: Optional[str] = None
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Environment":
        return cls(
            id=d.get("id"),
            name=d.get("name"),
            domain=d.get("domain"),
            is_default=d.get("is_default"),
            production=d.get("production"),
            synced_at=d.get("synced_at"),
            inserted_at=d.get("inserted_at"),
            updated_at=d.get("updated_at"),
            raw=d,
        )


@dataclass(frozen=True)
class ApplicationEnvironment:
    """An application placed in an environment at a base URL."""

    id: str
    application_id: Optional[str]
    environment_id: Optional[str]
    base_url: Optional[str]
    synced_at: Optional[str] = None
    inserted_at: Optional[str] = None
    updated_at: Optional[str] = None
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ApplicationEnvironment":
        return cls(
            id=d.get("id"),
            application_id=d.get("application_id"),
            environment_id=d.get("environment_id"),
            base_url=d.get("base_url"),
            synced_at=d.get("synced_at"),
            inserted_at=d.get("inserted_at"),
            updated_at=d.get("updated_at"),
            raw=d,
        )


@dataclass(frozen=True)
class CredentialEnvironment:
    id: str
    name: Optional[str]
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CredentialEnvironment":
        return cls(id=d.get("id"), name=d.get("name"), raw=d)


@dataclass(frozen=True)
class PreviousSecret:
    """The secret a rotation replaced, while it still authenticates."""

    last_4: Optional[str]
    expires_at: Optional[str]
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PreviousSecret":
        return cls(last_4=d.get("last_4"), expires_at=d.get("expires_at"), raw=d)


@dataclass(frozen=True)
class Credential:
    """
    A runtime client credential. ``client_secret`` is set only on the answer
    to a create or a rotate -- the one time the API returns it -- and is
    ``None`` on every read. It is left out of ``repr`` (as is ``raw``, which
    holds it too), and this SDK never logs it: store it where your runtime
    reads its credentials, and do not print it.
    """

    id: str
    client_id: Optional[str]
    secret_last_4: Optional[str]
    application_environment_id: Optional[str]
    application_id: Optional[str] = None
    environment: Optional[CredentialEnvironment] = None
    previous_secret: Optional[PreviousSecret] = None
    expired_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    client_secret: Optional[str] = field(default=None, repr=False)
    raw: Dict[str, Any] = field(**_RAW)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Credential":
        return cls(
            id=d.get("id"),
            client_id=d.get("client_id"),
            secret_last_4=d.get("secret_last_4"),
            application_environment_id=d.get("application_environment_id"),
            application_id=d.get("application_id"),
            environment=_opt(d.get("environment"), CredentialEnvironment),
            previous_secret=_opt(d.get("previous_secret"), PreviousSecret),
            expired_at=d.get("expired_at"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
            client_secret=d.get("client_secret"),
            raw=d,
        )
