"""Request manager exceptions for HTTP status mapping."""


class RequestLogCreationError(Exception):
    """Raised when RequestLog cannot be created (durable accept record failed).

    Callers should return HTTP 503 Service Unavailable so clients can retry.
    """


class SessionLockTimeoutError(Exception):
    """Raised when session lock cannot be acquired within timeout.

    Indicates too many concurrent requests for the same session.
    Callers should return HTTP 503 Service Unavailable so clients can retry.
    """
