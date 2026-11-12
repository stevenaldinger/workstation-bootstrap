"""Download and unpack the pinned Tart release."""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from prove_engine.model import ProofError


def download(url: str, destination: Path) -> None:
    if not url.startswith("https://"):
        raise ProofError(f"refusing to download from a non-https address: {url}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=destination.parent, suffix=".part")
    try:
        # The scheme was checked above, so this opens only an https address.
        with os.fdopen(fd, "wb") as handle, urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310
            shutil.copyfileobj(response, handle)
        os.replace(tmp_name, destination)
    except OSError as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise ProofError(f"download failed for {url}: {exc}") from exc


def unpack_tart(archive: Path, release_dir: Path) -> None:
    staging = Path(tempfile.mkdtemp(dir=release_dir, prefix="unpack-"))
    try:
        with tarfile.open(archive) as tar:
            tar.extractall(staging, filter="data")
        if not (staging / "tart.app").is_dir():
            raise ProofError("the Tart archive has no tart.app")
        shutil.rmtree(release_dir / "tart.app", ignore_errors=True)
        os.replace(staging / "tart.app", release_dir / "tart.app")
        if (staging / "LICENSE").is_file():
            os.replace(staging / "LICENSE", release_dir / "LICENSE")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
