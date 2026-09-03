from __future__ import annotations

import json
import os
from pathlib import Path


SETTINGS_VERSION = 1
REQUIRED_ROOT_NAME = "indiegala"


class InstallRootError(ValueError):
    pass


def data_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        root = Path(local_app_data)
    else:
        root = Path(__file__).resolve().parent
    return root / "melcom-creations" / "GOG Galaxy Integrations" / "IndieGala"


def settings_path() -> Path:
    return data_root() / "settings.json"


def normalize_install_root(value: str, *, create: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InstallRootError("Enter the full path to your IndieGala folder")

    expanded = os.path.expandvars(value.strip().strip('"')).rstrip("\\/")
    if not expanded:
        raise InstallRootError("Enter the full path to your IndieGala folder")
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        raise InstallRootError("The IndieGala folder path must be absolute")

    normalized = Path(os.path.abspath(os.path.normpath(str(path))))
    if normalized.name.casefold() != REQUIRED_ROOT_NAME:
        raise InstallRootError('The selected folder must be named "IndieGala"')

    if create:
        try:
            normalized.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise InstallRootError("The IndieGala folder could not be created") from error

    if not normalized.is_dir():
        raise InstallRootError("The IndieGala folder does not exist")
    return str(normalized)


def load_install_root() -> str | None:
    path = settings_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if payload.get("version") != SETTINGS_VERSION:
            return None
        return normalize_install_root(payload.get("install_root", ""))
    except (OSError, TypeError, ValueError, json.JSONDecodeError, InstallRootError):
        return None


def save_install_root(install_root: str) -> str:
    normalized = normalize_install_root(install_root, create=True)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    payload = {
        "version": SETTINGS_VERSION,
        "install_root": normalized,
    }
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return normalized
