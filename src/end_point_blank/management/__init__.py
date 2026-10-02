"""
The EndPointBlank organization management API client.

::

    from end_point_blank.management import ManagementClient, NotFoundError

    mgmt = ManagementClient(key="epb_mk_...")
    for package in mgmt.iter_api_packages():
        print(package.name)

Separate from the runtime SDK: it is configured only by its constructor and
sends only the management key. See :class:`ManagementClient`.
"""
from .client import DEFAULT_BASE_URL, ManagedClient, ManagementClient
from .errors import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    ErrorCode,
    IdempotencyReplayUnavailableError,
    InsufficientScopeError,
    InvalidResponseError,
    ManagementApiError,
    ManagementConnectionError,
    NotFoundError,
    PlanLimitError,
    RateLimitedError,
    RequestRefusedError,
    ServerError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from .models import (
    ApiPackage,
    ApiWarning,
    Application,
    ApplicationEnvironment,
    ClaimInvite,
    Client,
    Contact,
    Credential,
    CredentialEnvironment,
    Deleted,
    Endpoint,
    EndpointRef,
    Environment,
    Grant,
    KeyInfo,
    Organization,
    OrganizationRef,
    PackageAssignment,
    PackageEndpoint,
    PackageEndpointAdded,
    Page,
    PreAssignments,
    PreviousSecret,
    Refusal,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "ManagedClient",
    "ManagementClient",
    # errors
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "ErrorCode",
    "IdempotencyReplayUnavailableError",
    "InsufficientScopeError",
    "InvalidResponseError",
    "ManagementApiError",
    "ManagementConnectionError",
    "NotFoundError",
    "PlanLimitError",
    "RateLimitedError",
    "RequestRefusedError",
    "ServerError",
    "ServiceUnavailableError",
    "ValidationFailedError",
    # models
    "ApiPackage",
    "ApiWarning",
    "Application",
    "ApplicationEnvironment",
    "ClaimInvite",
    "Client",
    "Contact",
    "Credential",
    "CredentialEnvironment",
    "Deleted",
    "Endpoint",
    "EndpointRef",
    "Environment",
    "Grant",
    "KeyInfo",
    "Organization",
    "OrganizationRef",
    "PackageAssignment",
    "PackageEndpoint",
    "PackageEndpointAdded",
    "Page",
    "PreAssignments",
    "PreviousSecret",
    "Refusal",
]
