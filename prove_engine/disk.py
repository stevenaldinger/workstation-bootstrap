"""Make a guest's disk bigger than the image shipped it.

The pinned image's partition table ends with a recovery partition. After `tart set
--disk-size` the extra space lies behind that partition, and macOS cannot grow its system
container across it (SIP forbids moving the recovery partition from inside the guest). This
module moves the recovery partition to the end of the raw disk file, while the machine is
stopped, and rewrites both copies of the GUID partition table. The guest then grows its
container into the freed space with `diskutil apfs resizeContainer`.
"""

from __future__ import annotations

import struct
import uuid
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from prove_engine.model import ProofError, Runner

SECTOR = 512
HEADER_FORMAT = "<8sIIIIQQQQ16sQIII"
HEADER_SIZE = 92
ENTRY_FORMAT = "<16s16sQQQ72s"
ENTRY_SIZE = 128
SIGNATURE = b"EFI PART"
ALIGN_SECTORS = 8
COPY_CHUNK = 8 * 1024 * 1024
RECOVERY_TYPE = uuid.UUID("52637672-7900-11AA-AA11-00306543ECAC")
CONTAINER_TYPE = uuid.UUID("7C3457EF-0000-11AA-AA11-00306543ECAC")


@dataclass
class Partition:
    type_guid: bytes
    unique_guid: bytes
    first: int
    last: int
    attributes: int
    name: bytes

    @property
    def sectors(self) -> int:
        return self.last - self.first + 1

    def pack(self) -> bytes:
        return struct.pack(
            ENTRY_FORMAT,
            self.type_guid,
            self.unique_guid,
            self.first,
            self.last,
            self.attributes,
            self.name,
        )


@dataclass
class Table:
    disk_guid: bytes
    entry_count: int
    partitions: list[Partition | None]


def read_table(image: Path) -> Table:
    """Parse the primary GUID partition table of a raw disk file."""
    with image.open("rb") as handle:
        handle.seek(SECTOR)
        header = handle.read(HEADER_SIZE)
        fields = struct.unpack(HEADER_FORMAT, header)
        if fields[0] != SIGNATURE:
            raise ProofError(f"{image.name} has no GUID partition table")
        entry_lba, count, size = fields[10], fields[11], fields[12]
        if size != ENTRY_SIZE:
            raise ProofError(f"unexpected partition entry size {size}")
        handle.seek(entry_lba * SECTOR)
        raw = handle.read(count * size)
    partitions: list[Partition | None] = []
    for index in range(count):
        entry = struct.unpack(ENTRY_FORMAT, raw[index * size : (index + 1) * size])
        partitions.append(None if entry[0] == bytes(16) else Partition(*entry))
    return Table(fields[9], count, partitions)


def build_header(
    table: Table,
    *,
    current: int,
    backup: int,
    first_usable: int,
    last_usable: int,
    entries_lba: int,
) -> bytes:
    entries = b"".join((p.pack() if p else bytes(ENTRY_SIZE)) for p in table.partitions)
    fields = [SIGNATURE, 0x10000, HEADER_SIZE, 0, 0, current, backup, first_usable, last_usable]
    fields += [table.disk_guid, entries_lba, table.entry_count, ENTRY_SIZE, zlib.crc32(entries)]
    header = struct.pack(HEADER_FORMAT, *fields)
    crc = zlib.crc32(header)
    return struct.pack(HEADER_FORMAT, *fields[:3], crc, *fields[4:])


def write_table(image: Path, table: Table, total_sectors: int) -> None:
    """Write the primary and backup tables for a disk of `total_sectors` sectors."""
    entries = b"".join((p.pack() if p else bytes(ENTRY_SIZE)) for p in table.partitions)
    entry_sectors = -(-len(entries) // SECTOR)
    last_usable = total_sectors - 2 - entry_sectors
    backup_entries_lba = last_usable + 1
    primary = build_header(
        table, current=1, backup=total_sectors - 1, first_usable=34, last_usable=last_usable,
        entries_lba=2,
    )  # fmt: skip
    backup = build_header(
        table, current=total_sectors - 1, backup=1, first_usable=34, last_usable=last_usable,
        entries_lba=backup_entries_lba,
    )  # fmt: skip
    with image.open("r+b") as handle:
        handle.seek(SECTOR)
        handle.write(primary.ljust(SECTOR, b"\0"))
        handle.seek(2 * SECTOR)
        handle.write(entries.ljust(entry_sectors * SECTOR, b"\0"))
        handle.seek(backup_entries_lba * SECTOR)
        handle.write(entries.ljust(entry_sectors * SECTOR, b"\0"))
        handle.seek((total_sectors - 1) * SECTOR)
        handle.write(backup.ljust(SECTOR, b"\0"))


def copy_sectors(image: Path, source: int, destination: int, count: int) -> None:
    """Copy `count` sectors inside the file; the ranges must not overlap."""
    remaining = count * SECTOR
    with image.open("r+b") as handle:
        offset = 0
        while remaining > 0:
            size = min(COPY_CHUNK, remaining)
            handle.seek(source * SECTOR + offset)
            data = handle.read(size)
            handle.seek(destination * SECTOR + offset)
            handle.write(data)
            offset += size
            remaining -= size


def find_recovery(table: Table) -> Partition:
    recovery = [
        p for p in table.partitions if p and uuid.UUID(bytes_le=p.type_guid) == RECOVERY_TYPE
    ]
    if len(recovery) != 1:
        raise ProofError("expected exactly one recovery partition")
    return recovery[0]


def usable_end(image: Path, table: Table) -> int:
    """The last sector a partition may use on the disk file as it is now."""
    entry_sectors = -(-(table.entry_count * ENTRY_SIZE) // SECTOR)
    return image.stat().st_size // SECTOR - 2 - entry_sectors


def recovery_at_end(image: Path) -> bool:
    """Whether the recovery partition already ends within one alignment unit of the disk end."""
    table = read_table(image)
    return usable_end(image, table) - find_recovery(table).last < ALIGN_SECTORS


def grow_layout(image: Path) -> bool:
    """Move the recovery partition to the end of the disk file; False when already there.

    The disk file must already have its final size (`tart set --disk-size`) and no machine
    may be using it.
    """
    total_sectors = image.stat().st_size // SECTOR
    table = read_table(image)
    last_usable = usable_end(image, table)
    part = find_recovery(table)
    gap = last_usable - part.last
    if gap < 0:
        raise ProofError("the disk file is smaller than its partition table")
    if gap < ALIGN_SECTORS:
        write_table(image, table, total_sectors)
        return False
    new_first = (last_usable - part.sectors + 1) // ALIGN_SECTORS * ALIGN_SECTORS
    if new_first <= part.last:
        raise ProofError("the disk is too small to move the recovery partition")
    copy_sectors(image, part.first, new_first, part.sectors)
    part.first, part.last = new_first, new_first + part.sectors - 1
    write_table(image, table, total_sectors)
    return True


def parse_attached(output: str) -> tuple[str, str]:
    """The whole-disk node and the system container's partition node from `hdiutil attach`."""
    whole = ""
    container = ""
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        node, kind = fields[0], fields[1]
        if kind == "GUID_partition_scheme" and not whole:
            whole = node
        elif kind == "Apple_APFS" and not container:
            container = node
    if not whole or not container:
        raise ProofError(f"cannot read the attached disk layout:\n{output}")
    return whole, container


def grow_system_container(image: Path, runner: Runner, env: Mapping[str, str]) -> None:
    """Grow the system APFS container into the space behind the (moved) recovery partition.

    The raw disk file is attached to the host without mounting anything, resized with the
    host's `diskutil`, and detached again, so the guest sees one large system container.
    """
    attach = ["hdiutil", "attach", "-nomount", "-imagekey", "diskimage-class=CRawDiskImage"]
    attached = runner([*attach, str(image)], env=env, timeout=120)
    if attached.exit_code != 0:
        raise ProofError(f"hdiutil attach failed: {attached.stderr.strip()}")
    whole, container = parse_attached(attached.stdout)
    try:
        resized = runner(
            ["diskutil", "apfs", "resizeContainer", container, "0"], env=env, timeout=600
        )
        if resized.exit_code != 0:
            detail = (resized.stderr or resized.stdout).strip()
            raise ProofError(f"resizing the system container failed: {detail}")
    finally:
        runner(["hdiutil", "detach", whole, "-force"], env=env, timeout=120)
