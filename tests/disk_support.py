"""A synthetic raw disk file with the layout of the pinned image, for the disk and engine tests."""

from __future__ import annotations

import uuid
from pathlib import Path

from prove_engine.disk import (
    CONTAINER_TYPE,
    RECOVERY_TYPE,
    SECTOR,
    Partition,
    Table,
    find_recovery,
    grow_layout,
    read_table,
    system_container,
    write_table,
)

OLD_SECTORS = 4096
NEW_SECTORS = 16384
CONTAINER = (40, 2039)
RECOVERY = (2040, 4023)


def make_partition(kind: uuid.UUID, first: int, last: int, name: str) -> Partition:
    return Partition(
        kind.bytes_le,
        uuid.uuid4().bytes_le,
        first,
        last,
        0,
        name.encode("utf-16-le").ljust(72, b"\0"),
    )


def write_layout(path: Path, partitions: list[Partition], sectors: int) -> None:
    table = Table(uuid.uuid4().bytes_le, 128, [*partitions, *[None] * (128 - len(partitions))])
    path.write_bytes(bytes(sectors * SECTOR))
    write_table(path, table, sectors)


def make_disk(path: Path, *, recovery_marker: bytes = b"RECOVERY-DATA") -> Path:
    """A raw disk file with a container and a recovery partition, grown after the layout."""
    write_layout(
        path,
        [
            make_partition(CONTAINER_TYPE, *CONTAINER, "container"),
            make_partition(RECOVERY_TYPE, *RECOVERY, "recovery"),
        ],
        OLD_SECTORS,
    )
    with path.open("r+b") as handle:
        handle.seek(RECOVERY[0] * SECTOR)
        handle.write(recovery_marker)
        handle.seek(RECOVERY[1] * SECTOR)
        handle.write(b"LAST-SECTOR")
        handle.truncate(NEW_SECTORS * SECTOR)
    return path


def resize_container(path: Path) -> None:
    """What `diskutil apfs resizeContainer <node> 0` does to the table: fill the free space."""
    table = read_table(path)
    recovery = find_recovery(table)
    system_container(table).last = recovery.first - 1 - 8
    write_table(path, table, path.stat().st_size // SECTOR)


def stage_grown_base(image: Path) -> None:
    """Leave a base disk at `image` in the finished state."""
    image.parent.mkdir(parents=True, exist_ok=True)
    make_disk(image)
    grow_layout(image)
    resize_container(image)
