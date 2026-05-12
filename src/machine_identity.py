from __future__ import annotations

import hashlib
import socket
import subprocess
import sys
import uuid

_CACHED_FINGERPRINT: str | None = None


def _hash_components(components: list[str]) -> str:
    raw = "|".join(components)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _base_components() -> list[str]:
    mac = ":".join(
        ["{:02x}".format((uuid.getnode() >> e) & 0xFF) for e in range(0, 2 * 6, 2)][::-1]
    )
    return [mac, socket.gethostname()]


def _windows_machine_guid() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value).strip()
    except Exception:
        return ""


def _legacy_windows_disk_serial(timeout: float = 2.0) -> str:
    if sys.platform != "win32":
        return ""
    try:
        result = subprocess.run(
            ["wmic", "diskdrive", "get", "SerialNumber", "/value"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def get_machine_fingerprint() -> str:
    """Stable machine fingerprint used for machine-bound storage keys."""
    global _CACHED_FINGERPRINT
    if _CACHED_FINGERPRINT:
        return _CACHED_FINGERPRINT

    components = _base_components()
    if sys.platform == "win32":
        machine_guid = _windows_machine_guid()
        if machine_guid:
            components.append(f"machine-guid:{machine_guid}")
        else:
            components.append(_legacy_windows_disk_serial(timeout=1.0))

    _CACHED_FINGERPRINT = _hash_components(components)
    return _CACHED_FINGERPRINT


def get_machine_fingerprint_candidates() -> list[str]:
    """Return current and legacy fingerprints that identify this machine.

    The first public builds used `wmic diskdrive` in the fingerprint. That is
    slow and can drift on Windows, so the primary fingerprint now uses the
    registry MachineGuid. Legacy candidates let the launcher rebind an existing
    same-machine data folder once without weakening transfer checks for copies.
    """
    candidates = [get_machine_fingerprint()]

    if sys.platform == "win32":
        legacy = _hash_components(_base_components() + [_legacy_windows_disk_serial(timeout=2.0)])
        if legacy not in candidates:
            candidates.append(legacy)

    return candidates
