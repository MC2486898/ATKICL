"""公开的故障类型与 JSON 错误格式。"""


class SessionError(Exception):
    def __init__(self, code: int, state: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.state = state
        self.details = details

    def as_dict(self):
        return {"ok": False, "state": self.state, "message": str(self),
                "exit_code": self.code, **self.details}
