"""The starter's settings: a typed view of start.toml, loaded once at startup."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from start_base import StageError

CONFIG_NAME = "start.toml"


@dataclass(frozen=True)
class IdentityConfig:
    item_title: str
    vault: str
    key_file_name: str


@dataclass(frozen=True)
class RepositoryConfig:
    url: str
    clone_dir: Path
    hooks_path: str


@dataclass(frozen=True)
class StartConfig:
    identity: IdentityConfig
    repository: RepositoryConfig


def missing_keys(raw: dict[str, object], required: dict[str, tuple[str, ...]]) -> list[str]:
    """Every `section.key` that is absent or not a non-empty string; names only, never values."""
    missing = []
    for section, keys in required.items():
        table = raw.get(section)
        for key in keys:
            value = table.get(key) if isinstance(table, dict) else None
            if not isinstance(value, str) or not value.strip():
                missing.append(f"{section}.{key}")
    return missing


def load_config(path: Path) -> StartConfig:
    """Read start.toml; stop naming every missing setting if the file is incomplete."""
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise StageError(f"cannot read the starter settings {path.name}: {exc}") from exc
    missing = missing_keys(
        raw,
        {
            "identity": ("item_title", "vault", "key_file_name"),
            "repository": ("url", "clone_dir", "hooks_path"),
        },
    )
    if missing:
        raise StageError(f"{path.name} is missing: {', '.join(missing)}")
    identity, repository = raw["identity"], raw["repository"]
    return StartConfig(
        IdentityConfig(identity["item_title"], identity["vault"], identity["key_file_name"]),
        RepositoryConfig(
            repository["url"], Path(repository["clone_dir"]), repository["hooks_path"]
        ),
    )
