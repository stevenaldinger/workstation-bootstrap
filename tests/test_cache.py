"""The cache folder: purge removes only the engine's own data and refuses anything else."""

from __future__ import annotations

from pathlib import Path

import pytest
from support import FakeRunner, make_engine, result_of

from prove_engine import Engine, ProofError, cache, cli
from prove_engine.lock import HostLock


def engine_with_data(tmp_path: Path) -> Engine:
    """An engine whose cache root holds engine data and a file the engine did not write."""
    engine = make_engine(tmp_path, FakeRunner())
    cache.claim_root(engine.cache_root)
    engine.tart_home.mkdir()
    (engine.tart_home / "disk.img").write_text("image", encoding="utf-8")
    engine.logs.mkdir()
    (engine.cache_root / "askpass.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return engine


def test_purge_removes_the_engine_folders_and_the_marker(tmp_path: Path) -> None:
    engine = engine_with_data(tmp_path)
    engine.env = {**engine.env, "HOME": str(tmp_path / "home")}
    assert engine.cleanup(purge=True) == []
    assert not engine.cache_root.exists()


def test_purge_leaves_files_it_did_not_create(tmp_path: Path) -> None:
    engine = engine_with_data(tmp_path)
    (engine.cache_root / "start.py").write_text("starter file", encoding="utf-8")
    engine.env = {**engine.env, "HOME": str(tmp_path / "home")}
    engine.cleanup(purge=True)
    assert (engine.cache_root / "start.py").read_text(encoding="utf-8") == "starter file"
    assert not engine.tart_home.exists()
    assert not (engine.cache_root / cache.MARKER).exists()


def test_purge_refuses_the_home_folder_and_deletes_nothing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    (home / "Documents" / "thesis.txt").write_text("keep", encoding="utf-8")
    (home / "tart-home").mkdir()
    runner = FakeRunner()
    engine = Engine(
        home,
        host=make_engine(tmp_path, runner).host,
        env={"HOME": str(home)},
        vm_lock=tmp_path / "vm.lock",
        runner=runner,
    )
    with pytest.raises(ProofError, match="home folder"):
        engine.cleanup(purge=True)
    assert (home / "Documents" / "thesis.txt").read_text(encoding="utf-8") == "keep"
    assert (home / "tart-home").is_dir()
    assert runner.calls == []


def test_purge_refuses_a_folder_that_holds_the_home_folder(tmp_path: Path) -> None:
    home = tmp_path / "users" / "me"
    home.mkdir(parents=True)
    with pytest.raises(ProofError, match="home folder"):
        cache.check_purgeable(tmp_path / "users", home)
    with pytest.raises(ProofError, match="home folder"):
        cache.check_purgeable(Path("/"), home)


def test_purge_refuses_a_folder_the_engine_did_not_create(tmp_path: Path) -> None:
    other = tmp_path / "somewhere"
    (other / "tart-home").mkdir(parents=True)
    with pytest.raises(ProofError, match="no .prove-engine marker"):
        cache.check_purgeable(other, tmp_path / "home")
    assert (other / "tart-home").is_dir()


def test_purge_refuses_a_marker_that_is_not_the_engines(tmp_path: Path) -> None:
    other = tmp_path / "somewhere"
    other.mkdir()
    (other / cache.MARKER).write_text("something else\n", encoding="utf-8")
    with pytest.raises(ProofError, match="marker"):
        cache.check_purgeable(other, tmp_path / "home")


def test_purge_unlinks_a_symlinked_engine_folder_without_following_it(tmp_path: Path) -> None:
    engine = engine_with_data(tmp_path)
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "file").write_text("keep", encoding="utf-8")
    engine.logs.rmdir()
    engine.logs.symlink_to(precious, target_is_directory=True)
    engine.env = {**engine.env, "HOME": str(tmp_path / "home")}
    engine.cleanup(purge=True)
    assert (precious / "file").read_text(encoding="utf-8") == "keep"


def test_a_refused_purge_does_not_reap_or_touch_the_lock(tmp_path: Path) -> None:
    runner = FakeRunner([("list", result_of("wsrun-x-1\n"))])
    engine = make_engine(tmp_path, runner)
    engine.tart.parent.mkdir(parents=True)
    engine.tart.write_text("", encoding="utf-8")
    with pytest.raises(ProofError, match="marker"):
        engine.cleanup(purge=True)
    assert runner.calls == []


def test_the_engine_marks_its_root_on_first_use(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, FakeRunner())
    assert not engine.cache_root.exists()
    with pytest.raises(ProofError):
        engine.ensure_tart()
    assert cache.is_engine_root(engine.cache_root)


def test_the_default_root_is_not_the_starters_download_folder() -> None:
    assert cli.DEFAULT_CACHE_ROOT.parent == cli.LEGACY_CACHE_ROOT
    assert cli.DEFAULT_CACHE_ROOT != cli.LEGACY_CACHE_ROOT


def test_migration_moves_engine_data_out_of_the_shared_folder(tmp_path: Path) -> None:
    legacy, root = tmp_path / "shared", tmp_path / "shared" / "prove"
    (legacy / "tart-home" / "vms").mkdir(parents=True)
    (legacy / "tart" / "2.41.1").mkdir(parents=True)
    (legacy / "logs").mkdir()
    (legacy / "start.py").write_text("starter file", encoding="utf-8")
    assert cache.migrate_legacy_root(legacy, root) == ["tart", "tart-home", "logs"]
    assert (root / "tart-home" / "vms").is_dir()
    assert cache.is_engine_root(root)
    assert (legacy / "start.py").read_text(encoding="utf-8") == "starter file"
    assert not (legacy / "tart-home").exists()


def test_migration_does_nothing_without_engine_data_or_with_an_existing_root(
    tmp_path: Path,
) -> None:
    legacy, root = tmp_path / "shared", tmp_path / "shared" / "prove"
    legacy.mkdir()
    (legacy / "start.py").write_text("starter file", encoding="utf-8")
    assert cache.migrate_legacy_root(legacy, root) == []
    assert not root.exists()
    (legacy / "tart-home").mkdir()
    root.mkdir()
    assert cache.migrate_legacy_root(legacy, root) == []
    assert (legacy / "tart-home").is_dir()


def test_main_migrates_under_the_vm_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / "shared"
    root = legacy / "prove"
    (legacy / "tart-home").mkdir(parents=True)
    monkeypatch.setattr(cli, "LEGACY_CACHE_ROOT", legacy)
    monkeypatch.setattr(cli, "DEFAULT_CACHE_ROOT", root)
    monkeypatch.setattr(cli, "DEFAULT_VM_LOCK", tmp_path / "vm.lock")
    HostLock(tmp_path / "vm.lock", "other-run").acquire()
    assert cli.main(["--cache-root", str(root), "list"]) == 2
    assert (legacy / "tart-home").is_dir()
