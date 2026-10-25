"""Failures the workbench can show to a browser. Messages never include secrets."""


class WorkbenchError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(code)
