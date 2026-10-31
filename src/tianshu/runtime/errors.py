"""Safe application errors, independent from database and web frameworks."""


class AppError(Exception):
    def __init__(self, code: str, message: str = "", *, status: int = 400, details=None):
        self.code, self.message, self.status = code, message or code, status
        self.details = details
        super().__init__(code)
