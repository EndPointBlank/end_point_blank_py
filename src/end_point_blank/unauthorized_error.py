class UnauthorizedError(Exception):
    """
    Raised when a request fails authentication or authorization.

    This exception is intentionally not logged by the middleware,
    as unauthorized access attempts are expected to occur.
    """
