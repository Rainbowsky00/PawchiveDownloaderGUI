from .models import (
    DateNamingMode,
    DownloadEvent,
    DownloadRequest,
    DownloadTask,
    FileItem,
    PostItem,
)

__all__ = [
    "DateNamingMode",
    "DownloadEvent",
    "DownloadRequest",
    "DownloadTask",
    "DownloadEngine",
    "FileItem",
    "PostItem",
]


def __getattr__(name: str):
    if name == "DownloadEngine":
        from .engine import DownloadEngine

        return DownloadEngine
    raise AttributeError(name)
