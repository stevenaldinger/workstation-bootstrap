"""The recovery-partition move and the host-side container resize, on synthetic disks."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
from disk_support import (
    CONTAINER,
    NEW_SECTORS,
    RECOVERY,
    make_disk,
    make_partition,
    resize_container,
    write_layout,
)
from support import FakeRunner, result_of

from prove_engine import ProofError
from prove_engine.disk import (
    CONTAINER_TYPE,
    ENTRY_SIZE,
    HEADER_FORMAT,
    HEADER_SIZE,
    RECOVERY_TYPE,
    SECTOR,
    SIGNATURE,
    copy_sectors,
    disk_is_grown,
    grow_layout,
    grow_system_container,
    parse_attached,
    read_table,
    recovery_at_end,
    write_table,
)


def header_fields(path: Path, lba: int) -> tuple[object, ...]:
    with path.open("rb") as handle:
        handle.seek(lba * SECTOR)
        return struct.unpack(HEADER_FORMAT, handle.read(HEADER_SIZE))


def check_header_crc(path: Path, lba: int) -> None:
    with path.open("rb") as handle:
        handle.seek(lba * SECTOR)
        raw = bytearray(handle.read(HEADER_SIZE))
    stored = struct.unpack_from("<I", raw, 16)[0]
    raw[16:20] = bytes(4)
    assert zlib.crc32(bytes(raw)) == stored


def test_the_recovery_partition_moves_to_the_end_with_its_data(tmp_path: Path) -> None:
    disk = make_disk(tmp_path / "disk.img")
    assert recovery_at_end(disk) is False
    assert grow_layout(disk) is True
    table = read_table(disk)
    recovery = next(p for p in table.partitions if p and p.type_guid == RECOVERY_TYPE.bytes_le)
    container = next(p for p in table.partitions if p and p.type_guid == CONTAINER_TYPE.bytes_le)
    assert (container.first, container.last) == CONTAINER
    assert recovery.first % 8 == 0
    assert recovery.last < NEW_SECTORS - 33
    assert recovery.sectors == RECOVERY[1] - RECOVERY[0] + 1
    assert recovery_at_end(disk)
    data = disk.read_bytes()
    assert data[recovery.first * SECTOR :][:13] == b"RECOVERY-DATA"
    last = (recovery.last) * SECTOR
    assert data[last : last + 11] == b"LAST-SECTOR"


def test_both_headers_describe_the_grown_disk_with_valid_checksums(tmp_path: Path) -> None:
    disk = make_disk(tmp_path / "disk.img")
    grow_layout(disk)
    primary = header_fields(disk, 1)
    backup = header_fields(disk, NEW_SECTORS - 1)
    assert primary[0] == SIGNATURE
    assert primary[6] == NEW_SECTORS - 1
    assert backup[5] == NEW_SECTORS - 1
    assert backup[6] == 1
    assert primary[8] == backup[8] == NEW_SECTORS - 34
    check_header_crc(disk, 1)
    check_header_crc(disk, NEW_SECTORS - 1)
    entries = disk.read_bytes()[2 * SECTOR :][: 128 * ENTRY_SIZE]
    assert primary[13] == zlib.crc32(entries)


def test_a_second_pass_changes_nothing(tmp_path: Path) -> None:
    disk = make_disk(tmp_path / "disk.img")
    grow_layout(disk)
    before = disk.read_bytes()
    assert grow_layout(disk) is False
    assert disk.read_bytes() == before


def test_a_disk_too_small_to_move_the_partition_is_refused(tmp_path: Path) -> None:
    disk = make_disk(tmp_path / "disk.img")
    with disk.open("r+b") as handle:
        handle.truncate((RECOVERY[1] + 400) * SECTOR)
    with pytest.raises(ProofError, match="too small"):
        grow_layout(disk)


def test_a_file_without_a_partition_table_is_refused(tmp_path: Path) -> None:
    disk = tmp_path / "disk.img"
    disk.write_bytes(bytes(SECTOR * 64))
    with pytest.raises(ProofError, match="no GUID partition table"):
        read_table(disk)


def test_a_disk_file_smaller_than_its_table_is_refused(tmp_path: Path) -> None:
    disk = make_disk(tmp_path / "disk.img")
    with disk.open("r+b") as handle:
        handle.truncate(2000 * SECTOR)
    with pytest.raises(ProofError, match="smaller than its partition table"):
        grow_layout(disk)


def test_a_table_without_exactly_one_recovery_partition_is_refused(tmp_path: Path) -> None:
    disk = make_disk(tmp_path / "disk.img")
    table = read_table(disk)
    table.partitions[1] = None
    write_table(disk, table, NEW_SECTORS)
    with pytest.raises(ProofError, match="exactly one recovery partition"):
        grow_layout(disk)


ATTACHED = (
    "/dev/disk4          \tGUID_partition_scheme          \t\n"
    "/dev/disk4s1        \tApple_APFS_ISC                 \t\n"
    "/dev/disk4s2        \tApple_APFS                     \t\n"
    "/dev/disk4s3        \tApple_APFS_Recovery            \t\n"
    "/dev/disk5          \tEF57347C-0000-11AA-AA11-0030654\t\n"
)


def test_the_attached_disk_and_its_system_container_are_picked_out() -> None:
    assert parse_attached(ATTACHED) == ("/dev/disk4", "/dev/disk4s2")


def test_an_attach_listing_without_a_container_is_refused() -> None:
    with pytest.raises(ProofError, match="cannot read the attached disk layout"):
        parse_attached("/dev/disk4 \tGUID_partition_scheme\n")


def test_the_container_is_resized_on_the_host_and_the_image_detached(tmp_path: Path) -> None:
    runner = FakeRunner([("hdiutil attach", result_of(ATTACHED))])
    grow_system_container(tmp_path / "disk.img", runner, {})
    assert [call[:3] for call in runner.calls] == [
        ("hdiutil", "attach", "-nomount"),
        ("diskutil", "apfs", "resizeContainer"),
        ("hdiutil", "detach", "/dev/disk4"),
    ]
    assert runner.calls[1][3:] == ("/dev/disk4s2", "0")


def test_a_failed_resize_still_detaches_the_image(tmp_path: Path) -> None:
    runner = FakeRunner(
        [
            ("hdiutil attach", result_of(ATTACHED)),
            ("resizeContainer", result_of("", 1, "gap is missing")),
        ]
    )
    with pytest.raises(ProofError, match="gap is missing"):
        grow_system_container(tmp_path / "disk.img", runner, {})
    assert runner.calls[-1][:2] == ("hdiutil", "detach")


def test_a_failed_attach_stops_before_any_resize(tmp_path: Path) -> None:
    runner = FakeRunner([("hdiutil attach", result_of("", 1, "resource busy"))])
    with pytest.raises(ProofError, match="resource busy"):
        grow_system_container(tmp_path / "disk.img", runner, {})
    assert [call[0] for call in runner.calls] == ["hdiutil"]


# --- Grown-state detection -----------------------------------------------------------


def test_a_disk_is_grown_only_after_both_the_move_and_the_container_resize(tmp_path: Path) -> None:
    disk = make_disk(tmp_path / "disk.img")
    assert disk_is_grown(disk) is False
    grow_layout(disk)
    assert disk_is_grown(disk) is False
    resize_container(disk)
    assert disk_is_grown(disk) is True


def test_a_resize_that_stopped_between_the_copy_and_the_table_write_is_repeated(
    tmp_path: Path,
) -> None:
    disk = make_disk(tmp_path / "disk.img")
    reference = make_disk(tmp_path / "reference.img")
    grow_layout(reference)
    expected = read_table(reference)
    # The copy finished and the table was not written.
    moved = next(p for p in expected.partitions if p and p.type_guid == RECOVERY_TYPE.bytes_le)
    copy_sectors(disk, RECOVERY[0], moved.first, moved.sectors)
    assert grow_layout(disk) is True
    recovery = next(
        p for p in read_table(disk).partitions if p and p.type_guid == RECOVERY_TYPE.bytes_le
    )
    assert recovery.first == moved.first
    assert disk.read_bytes()[recovery.first * SECTOR :][:13] == b"RECOVERY-DATA"


def test_overlapping_partitions_are_refused_before_anything_moves(tmp_path: Path) -> None:
    disk = tmp_path / "disk.img"
    write_layout(
        disk,
        [
            make_partition(CONTAINER_TYPE, 40, 2100, "container"),
            make_partition(RECOVERY_TYPE, 2040, 4023, "recovery"),
        ],
        NEW_SECTORS,
    )
    before = disk.read_bytes()
    with pytest.raises(ProofError, match="overlap"):
        grow_layout(disk)
    assert disk.read_bytes() == before


def test_a_partition_inside_the_table_area_is_refused(tmp_path: Path) -> None:
    disk = tmp_path / "disk.img"
    write_layout(
        disk,
        [
            make_partition(CONTAINER_TYPE, 10, 2039, "container"),
            make_partition(RECOVERY_TYPE, 2040, 4023, "recovery"),
        ],
        NEW_SECTORS,
    )
    with pytest.raises(ProofError, match="outside the disk's usable range"):
        grow_layout(disk)


def test_a_recovery_partition_that_is_not_last_is_refused(tmp_path: Path) -> None:
    disk = tmp_path / "disk.img"
    write_layout(
        disk,
        [
            make_partition(RECOVERY_TYPE, 40, 2039, "recovery"),
            make_partition(CONTAINER_TYPE, 2040, 4023, "container"),
        ],
        NEW_SECTORS,
    )
    with pytest.raises(ProofError, match="not the last partition"):
        grow_layout(disk)


def test_a_disk_without_an_apfs_container_is_refused(tmp_path: Path) -> None:
    disk = tmp_path / "disk.img"
    write_layout(disk, [make_partition(RECOVERY_TYPE, 40, 2039, "recovery")], NEW_SECTORS)
    with pytest.raises(ProofError, match="APFS container"):
        disk_is_grown(disk)


def test_an_unreadable_listing_still_detaches_the_attached_image(tmp_path: Path) -> None:
    runner = FakeRunner([("hdiutil attach", result_of("/dev/disk4 \tGUID_partition_scheme\n"))])
    with pytest.raises(ProofError, match="cannot read the attached disk layout"):
        grow_system_container(tmp_path / "disk.img", runner, {})
    assert runner.calls[-1][:3] == ("hdiutil", "detach", "/dev/disk4")
    assert not [call for call in runner.calls if call[0] == "diskutil"]


def test_a_detach_that_fails_after_a_good_resize_is_an_error(tmp_path: Path) -> None:
    runner = FakeRunner(
        [("hdiutil attach", result_of(ATTACHED)), ("hdiutil detach", result_of("", 1, "busy"))]
    )
    with pytest.raises(ProofError, match="detach /dev/disk4 failed: busy"):
        grow_system_container(tmp_path / "disk.img", runner, {})
