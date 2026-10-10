class ApiError(Exception):
    """Raised anywhere in the service layer; rendered as
    {"error": {"code", "message", "details"}} by the global handler."""

    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}
