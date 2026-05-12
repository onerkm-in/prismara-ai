from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from src.machine_identity import get_machine_fingerprint

_FORMAT_LEGACY = "mlmae-secure-v1"
_FORMAT = "mlmae-secure-v2"
_BACKUP_EXT = ".bak.json"
_MAX_BACKUPS = 25


class SecureStorageDecodeError(Exception):
    """A wrapped file exists but its payload could not be decoded.

    Typical cause: the file is machine-bound (XOR key derived from this
    machine's fingerprint) and was written on a different machine, or the
    payload checksum no longer matches. Callers that need to distinguish
    "file is missing" from "file is unreadable here" should pass strict=True
    to load_json and catch this exception.
    """


def _derive_key() -> bytes:
    fingerprint = os.environ.get("MLMAE_STORAGE_KEY_FINGERPRINT_OVERRIDE", "").strip()
    if not fingerprint:
        fingerprint = get_machine_fingerprint()
    return hashlib.sha256(fingerprint.encode("utf-8")).digest()


def _xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _legacy_encode_text(plain: str) -> str:
    key = _derive_key()
    cipher = _xor_bytes(plain.encode("utf-8"), key)
    return base64.b64encode(cipher).decode("ascii")


def _legacy_decode_text(encoded: str) -> str:
    key = _derive_key()
    raw = base64.b64decode(encoded.encode("ascii"))
    plain = _xor_bytes(raw, key)
    return plain.decode("utf-8")


def encode_text(plain: str) -> str:
    """Encrypt text with AES-GCM using the machine-bound storage key."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:  # pragma: no cover - build/runtime dependency guard
        raise RuntimeError("cryptography is required for encrypted local storage") from exc

    key = _derive_key()
    nonce = os.urandom(12)
    cipher = AESGCM(key).encrypt(nonce, plain.encode("utf-8"), None)
    return base64.b64encode(nonce + cipher).decode("ascii")


def decode_text(encoded: str) -> str:
    """Decrypt AES-GCM text, falling back to legacy v1 XOR payloads."""
    raw = base64.b64decode(encoded.encode("ascii"))
    if len(raw) > 28:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            key = _derive_key()
            plain = AESGCM(key).decrypt(raw[:12], raw[12:], None)
            return plain.decode("utf-8")
        except Exception:
            pass

    return _legacy_decode_text(encoded)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _backup_root() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    data_dir = Path(os.environ.get("MLMAE_DATA_DIR", str(repo_root / "prismara")))
    if not data_dir.is_absolute():
        data_dir = repo_root / data_dir
    root = Path(os.environ.get("MLMAE_BACKUP_DIR", str(data_dir / "backups")))
    p = root if root.is_absolute() else repo_root / root
    p.mkdir(parents=True, exist_ok=True)
    return p


def _file_backup_dir(path: Path) -> Path:
    key = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    d = _backup_root() / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_backup(path: Path, content: str):
    bdir = _file_backup_dir(path)
    ts = int(time.time() * 1000)
    bfile = bdir / f"{ts}{_BACKUP_EXT}"
    bfile.write_text(content, encoding="utf-8")

    backups = sorted(bdir.glob(f"*{_BACKUP_EXT}"), reverse=True)
    for old in backups[_MAX_BACKUPS:]:
        try:
            old.unlink(missing_ok=True)
        except Exception:
            pass


def _restore_latest_backup(path: Path) -> bool:
    bdir = _file_backup_dir(path)
    backups = sorted(bdir.glob(f"*{_BACKUP_EXT}"), reverse=True)
    for bf in backups:
        try:
            text = bf.read_text(encoding="utf-8")
            json.loads(text)  # must be valid JSON wrapper/plain JSON
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return True
        except Exception:
            continue
    return False


def load_json(path: Path, default: Any = None, strict: bool = False) -> Any:
    """Load a JSON file written by save_json.

    Path-not-found and decode-failure are different conditions: when
    `strict=True`, an existing wrapped file whose payload cannot be decoded
    (XOR key mismatch, checksum mismatch, broken JSON inside the payload)
    raises SecureStorageDecodeError instead of silently returning `default`.
    Pass strict=True for files where the caller needs to distinguish "first
    run" from "this machine cannot read what's on disk" — for example
    neural_memory.json, credentials.json, config.json.
    """
    file_existed_originally = path.exists()

    if not path.exists():
        if not _restore_latest_backup(path):
            return default

    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        if _restore_latest_backup(path):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception as e2:
                if strict and file_existed_originally:
                    raise SecureStorageDecodeError(
                        f"{path.name} exists but cannot be read: {e2}"
                    ) from e2
                return default
        else:
            if strict and file_existed_originally:
                raise SecureStorageDecodeError(
                    f"{path.name} exists but cannot be read: {e}"
                ) from e
            return default

    try:
        obj = json.loads(text)
    except Exception as e:
        if _restore_latest_backup(path):
            try:
                obj = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e2:
                if strict and file_existed_originally:
                    raise SecureStorageDecodeError(
                        f"{path.name} contains invalid JSON: {e2}"
                    ) from e2
                return default
        else:
            if strict and file_existed_originally:
                raise SecureStorageDecodeError(
                    f"{path.name} contains invalid JSON: {e}"
                ) from e
            return default

    if isinstance(obj, dict) and obj.get("_format") in (_FORMAT, _FORMAT_LEGACY) and "payload" in obj:
        try:
            checksum = obj.get("checksum")
            if checksum and checksum != _sha256_text(str(obj["payload"])):
                raise ValueError("payload checksum mismatch")
            decoded = _legacy_decode_text(obj["payload"]) if obj.get("_format") == _FORMAT_LEGACY else decode_text(obj["payload"])
            return json.loads(decoded)
        except Exception as primary_err:
            if _restore_latest_backup(path):
                try:
                    obj2 = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(obj2, dict) and obj2.get("_format") in (_FORMAT, _FORMAT_LEGACY) and "payload" in obj2:
                        decoded = _legacy_decode_text(obj2["payload"]) if obj2.get("_format") == _FORMAT_LEGACY else decode_text(obj2["payload"])
                        return json.loads(decoded)
                    return obj2
                except Exception as backup_err:
                    if strict:
                        raise SecureStorageDecodeError(
                            f"{path.name} payload could not be decoded "
                            f"(machine fingerprint mismatch is the most likely "
                            f"cause). Primary: {primary_err}. Backup: {backup_err}."
                        ) from primary_err
                    return default
            if strict:
                raise SecureStorageDecodeError(
                    f"{path.name} payload could not be decoded and no readable "
                    f"backup is available (machine fingerprint mismatch is the "
                    f"most likely cause): {primary_err}"
                ) from primary_err
            return default

    return obj


def save_json(path: Path, data: Any, encode_at_rest: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)

    if encode_at_rest:
        payload = encode_text(json.dumps(data, separators=(",", ":")))
        wrapped = {
            "_format": _FORMAT,
            "payload": payload,
            "checksum": _sha256_text(payload),
            "updated_at": int(time.time()),
        }
        content = json.dumps(wrapped, indent=2)
    else:
        content = json.dumps(data, indent=2)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    _write_backup(path, content)
