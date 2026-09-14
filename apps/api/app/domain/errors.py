from __future__ import annotations


class PhoneOpsError(Exception):
    """Base class. Carries an HTTP status so routes stay thin."""

    status_code = 500
    code = "phoneops_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(PhoneOpsError):
    status_code = 404
    code = "not_found"


class ConflictError(PhoneOpsError):
    status_code = 409
    code = "conflict"


class ValidationError(PhoneOpsError):
    status_code = 422
    code = "validation_error"


class PolicyViolationError(PhoneOpsError):
    status_code = 403
    code = "policy_violation"


class CalleError(PhoneOpsError):
    status_code = 502
    code = "calle_error"

    def __init__(self, message: str, *, transient: bool = False, details: dict | None = None):
        super().__init__(message, details=details)
        self.transient = transient
