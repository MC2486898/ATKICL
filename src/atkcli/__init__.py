"""ATK 本机会话工具。使用 with Session(Config(...)) 保持一个只读 Connect 会话。"""
from .errors import SessionError

__version__ = "0.1.0"
__all__ = ["Config", "Session", "SessionError", "doctor", "status", "watch"]


def __getattr__(name):
    # 只有使用本机会话时才加载进程依赖；文件校验 API 只用标准库。
    if name in {"Config", "Session", "doctor", "status", "watch"}:
        from importlib import import_module
        value = getattr(import_module('.session', __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(name)
