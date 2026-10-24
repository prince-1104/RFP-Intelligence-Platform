"""Shared guard for local-only benchmark/evaluation scripts.

Settings come from ``rfp.cli.local_settings`` (never ``.env``) and are rejected unless Qdrant is a
loopback URL and no cloud storage is configured, so these scripts cannot write to Qdrant Cloud,
R2 or a production database.
"""

import ctypes
import ctypes.wintypes
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def local_only_settings():
    from rfp.cli import local_settings

    settings = local_settings(os.environ.get("RFP_LOCAL_QDRANT_URL", "http://127.0.0.1:6333"))
    host = urlparse(settings.qdrant_url).hostname
    if host not in LOOPBACK:
        raise SystemExit(f"refusing non-local Qdrant URL host {host!r}")
    if settings.qdrant_api_key.get_secret_value() or settings.r2_configured:
        raise SystemExit("refusing: cloud credentials present in local settings")
    return settings


class _Counters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("PageFaultCount", ctypes.wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def memory_mb() -> dict[str, float]:
    """Current and peak resident memory of this process in MiB (Windows working set or
    Linux/macOS ru_maxrss). Peak values are process-lifetime maxima."""
    if sys.platform == "win32":
        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        process = ctypes.windll.kernel32.GetCurrentProcess
        process.restype = ctypes.wintypes.HANDLE
        query = ctypes.windll.psapi.GetProcessMemoryInfo
        query.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(_Counters), ctypes.wintypes.DWORD]
        query.restype = ctypes.wintypes.BOOL
        if not query(process(), ctypes.byref(counters), counters.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return {
            "rss_mb": counters.WorkingSetSize / 2**20,
            "peak_rss_mb": counters.PeakWorkingSetSize / 2**20,
            "private_mb": counters.PagefileUsage / 2**20,
            "peak_private_mb": counters.PeakPagefileUsage / 2**20,
        }
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = peak / 2**20 if sys.platform == "darwin" else peak / 2**10
    return {"rss_mb": float("nan"), "peak_rss_mb": peak_mb}
