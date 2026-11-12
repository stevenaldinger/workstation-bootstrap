"""The engine's cache folder: an ownership marker, a safe purge, and the move from the old path."""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path

from prove_engine.model import ProofError

MARKER = ".prove-engine"
MARKER_TEXT = (
    "workstation-bootstrap prove engine cache\n"
    "`prove.py cleanup --purge` removes only the entries listed in prove_engine/cache.py.\n"
)
# Everything the engine writes under its cache root. Purge removes exactly these.
OWNED = ("tart", "tart-home", "logs", "askpass.sh")


def claim_root(root: Path) -> None:
    """Create the cache root and mark it as the engine's."""
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if not marker.exists():
        marker.write_text(MARKER_TEXT, encoding="utf-8")


def is_engine_root(root: Path) -> bool:
    marker = root / MARKER
    try:
        return marker.read_text(encoding="utf-8") == MARKER_TEXT
    except OSError:
        return False


def check_purgeable(root: Path, home: Path) -> None:
    """Refuse a root that is not the engine's own or that holds the user's home folder."""
    resolved, resolved_home = root.resolve(), home.resolve()
    if (
        resolved == resolved.parent
        or resolved == resolved_home
        or resolved in resolved_home.parents
    ):
        raise ProofError(f"refusing to purge {root}: it is, or contains, your home folder")
    if not is_engine_root(root):
        raise ProofError(
            f"refusing to purge {root}: it has no {MARKER} marker, so the engine did not create it"
        )


def purge_root(root: Path, home: Path) -> list[str]:
    """Remove the engine's own folders from the cache root; return the names removed.

    The root itself is removed only when nothing else is left in it.
    """
    check_purgeable(root, home)
    removed = []
    for name in OWNED:
        path = root / name
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            try:
                shutil.rmtree(path)
            except OSError as exc:
                raise ProofError(f"could not remove {path}: {exc}") from exc
        else:
            continue
        removed.append(name)
    (root / MARKER).unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        root.rmdir()
    return removed


def migrate_legacy_root(legacy: Path, root: Path) -> list[str]:
    """Move the engine's folders from the old shared cache root into its own folder.

    The engine used to share ~/.cache/workstation-bootstrap with the starter's download
    folder. Nothing moves when the new root already exists or the old one holds no engine data.
    """
    if root.exists() or not any((legacy / name).exists() for name in ("tart", "tart-home")):
        return []
    moved = []
    claim_root(root)
    for name in OWNED:
        source = legacy / name
        if source.exists():
            source.rename(root / name)
            moved.append(name)
    return moved
