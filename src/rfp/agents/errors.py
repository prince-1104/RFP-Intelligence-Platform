"""Safe extraction failures. Messages never include credentials or document text."""


class ExtractionError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(code)


class ModelError(ExtractionError):
    """The language-model call failed or was not configured. No substitute answer is invented."""
