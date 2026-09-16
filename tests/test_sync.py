"""Incremental sync of a Splice library into Logic's Apple Loops folder.

A sync run must be safe to repeat. The converter's collision handling suffixes
a taken name (Kick_01_2.caf), which is right for two different packs both
containing Kick_01 but catastrophic for a re-run: every sync would duplicate
the entire library. So the sync keeps a manifest of what it converted, keyed by
source path, and decides per file.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import importlib
sync = importlib.import_module("sync_splice_to_apple_loops")


def touch(path: Path, content=b"data"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture
def library(tmp_path):
    src = tmp_path / "Splice/sounds"
    for name in ("a.wav", "b.wav", "packs/deep/c.wav"):
        touch(src / name)
    return src


class TestPlanning:
    def test_a_first_run_treats_everything_as_new(self, library, tmp_path):
        plan = sync.plan(library, sync.Manifest(tmp_path / "m.json"),
                         tmp_path / "out")
        assert len(plan.new) == 3
        assert plan.unchanged == [] and plan.changed == []

    def test_a_second_run_has_nothing_to_do(self, library, tmp_path):
        manifest = sync.Manifest(tmp_path / "m.json")
        out = tmp_path / "out"
        for item in sync.plan(library, manifest, out).new:
            touch(item.output)
            manifest.record(item.source, item.output)

        plan = sync.plan(library, manifest, out)
        assert plan.new == [] and plan.changed == []
        assert len(plan.unchanged) == 3

    def test_a_newly_downloaded_sample_is_the_only_new_one(self, library, tmp_path):
        manifest = sync.Manifest(tmp_path / "m.json")
        out = tmp_path / "out"
        for item in sync.plan(library, manifest, out).new:
            touch(item.output)
            manifest.record(item.source, item.output)

        touch(library / "brand_new.wav")
        plan = sync.plan(library, manifest, out)
        assert [i.source.name for i in plan.new] == ["brand_new.wav"]

    def test_a_source_that_changed_is_reconverted(self, library, tmp_path):
        manifest = sync.Manifest(tmp_path / "m.json")
        out = tmp_path / "out"
        for item in sync.plan(library, manifest, out).new:
            touch(item.output)
            manifest.record(item.source, item.output)

        (library / "a.wav").write_bytes(b"different length of data")
        plan = sync.plan(library, manifest, out)
        assert [i.source.name for i in plan.changed] == ["a.wav"]

    def test_a_converted_file_deleted_by_hand_is_rebuilt(self, library, tmp_path):
        manifest = sync.Manifest(tmp_path / "m.json")
        out = tmp_path / "out"
        for item in sync.plan(library, manifest, out).new:
            touch(item.output)
            manifest.record(item.source, item.output)

        next(out.glob("*.caf")).unlink()
        plan = sync.plan(library, manifest, out)
        assert len(plan.new) + len(plan.changed) == 1


class TestNameAllocation:
    def test_two_packs_with_the_same_basename_get_distinct_outputs(self, tmp_path):
        src = tmp_path / "s"
        touch(src / "packA/Kick_01.wav")
        touch(src / "packB/Kick_01.wav")

        plan = sync.plan(src, sync.Manifest(tmp_path / "m.json"), tmp_path / "out")
        names = sorted(i.output.name for i in plan.new)
        assert names == ["Kick_01.caf", "Kick_01_2.caf"]

    def test_a_files_output_name_is_stable_across_runs(self, tmp_path):
        src = tmp_path / "s"
        touch(src / "packA/Kick_01.wav")
        touch(src / "packB/Kick_01.wav")
        manifest = sync.Manifest(tmp_path / "m.json")
        out = tmp_path / "out"

        first = {i.source: i.output for i in sync.plan(src, manifest, out).new}
        for source, output in first.items():
            touch(output)
            manifest.record(source, output)

        touch(src / "packC/Later.wav")
        second = sync.plan(src, manifest, out)
        assert {i.source: i.output for i in second.unchanged} == first


class TestManifestPersistence:
    def test_it_survives_a_reload(self, tmp_path):
        path = tmp_path / "m.json"
        m = sync.Manifest(path)
        m.record(tmp_path / "x.wav", tmp_path / "x.caf")
        m.save()

        assert json.loads(path.read_text())["entries"]
        assert sync.Manifest(path).knows(tmp_path / "x.wav")

    def test_a_corrupt_manifest_does_not_stop_the_sync(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text("{ this is not json")
        assert sync.Manifest(path).entries == {}


class TestAdoptingWorkFromBeforeTheManifestExisted:
    """A first run must not duplicate loops that are already there.

    The output folder can already hold .caf files -- from an earlier version of
    this toolkit, or from a run before the manifest existed. Allocating around
    them would suffix every single one and double the library.
    """

    def test_an_existing_output_is_adopted_not_duplicated(self, library, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "a.caf").write_bytes(b"caff")

        plan = sync.plan(library, sync.Manifest(tmp_path / "m.json"), out)
        assert [i.source.name for i in plan.adopted] == ["a.wav"]
        assert "a.wav" not in [i.source.name for i in plan.new]
        assert not any(i.output.name == "a_2.caf" for i in plan.new)

    def test_adoption_records_it_so_later_runs_stay_quiet(self, library, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "a.caf").write_bytes(b"caff")
        manifest = sync.Manifest(tmp_path / "m.json")

        first = sync.plan(library, manifest, out)
        for item in first.adopted:
            manifest.record(item.source, item.output)

        second = sync.plan(library, manifest, out)
        assert second.adopted == []
        assert [i.source.name for i in second.unchanged] == ["a.wav"]

    def test_rebuild_converts_adopted_files_instead_of_skipping_them(
            self, library, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "a.caf").write_bytes(b"caff")

        plan = sync.plan(library, sync.Manifest(tmp_path / "m.json"), out,
                         rebuild=True)
        assert "a.wav" in [i.source.name for i in plan.to_convert]
        assert plan.adopted == []

    def test_an_orphan_caf_with_no_source_is_left_alone(self, library, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "something_i_added_by_hand.caf").write_bytes(b"caff")

        plan = sync.plan(library, sync.Manifest(tmp_path / "m.json"), out)
        assert plan.adopted == []
        assert len(plan.new) == 3


class TestOneOutputPerSource:
    """Two sources must never be mapped to the same .caf.

    Found on a real run: 4 of 1,812 outputs were claimed twice, so one
    sample's audio was silently overwritten by another's. Adoption was the
    hole -- it matched on stem without claiming the name, so two sources with
    the same stem both adopted the same file.
    """

    def test_two_same_named_sources_cannot_both_adopt_one_output(self, tmp_path):
        src = tmp_path / "s"
        touch(src / "packA/Kick.wav")
        touch(src / "packB/Kick.wav")
        out = tmp_path / "out"
        out.mkdir()
        (out / "Kick.caf").write_bytes(b"caff")

        plan = sync.plan(src, sync.Manifest(tmp_path / "m.json"), out)
        outputs = [i.output for i in plan.adopted + plan.new + plan.changed]
        assert len(outputs) == len(set(outputs)), outputs

    def test_a_manifest_that_already_double_booked_an_output_is_repaired(self, tmp_path):
        src = tmp_path / "s"
        a = touch(src / "packA/Kick.wav")
        b = touch(src / "packB/Kick.wav")
        out = tmp_path / "out"
        out.mkdir()
        shared = out / "Kick.caf"
        shared.write_bytes(b"caff")

        manifest = sync.Manifest(tmp_path / "m.json")
        manifest.record(a, shared)
        manifest.record(b, shared)          # the corrupt state

        plan = sync.plan(src, manifest, out)
        outputs = [i.output for i in plan.unchanged + plan.new + plan.changed]
        assert len(outputs) == len(set(outputs)), outputs
        assert len(plan.changed) == 1, "the double-booked source must be redone"


class TestCaseInsensitiveFilesystems:
    """macOS treats Foo.caf and foo.caf as the same file. The sync must too.

    Found on a real library: two Splice samples named DropChordsDRY.wav and
    DropchordsDRY.wav were allocated two "distinct" output names that are one
    file on APFS, so one sample's audio overwrote the other's and one of 1,810
    owned samples was missing from the library.
    """

    def test_names_differing_only_by_case_get_separate_outputs(self, tmp_path):
        src = tmp_path / "s"
        touch(src / "packA/DropChords.wav")
        touch(src / "packB/Dropchords.wav")

        plan = sync.plan(src, sync.Manifest(tmp_path / "m.json"), tmp_path / "out")
        names = [i.output.name.casefold() for i in plan.new]
        assert len(names) == len(set(names)), [i.output.name for i in plan.new]

    def test_adoption_matches_an_existing_file_whatever_its_case(self, tmp_path):
        src = tmp_path / "s"
        touch(src / "Kick.wav")
        out = tmp_path / "out"
        out.mkdir()
        (out / "KICK.caf").write_bytes(b"caff")   # same file on macOS

        plan = sync.plan(src, sync.Manifest(tmp_path / "m.json"), out)
        assert not any(i.output.name.casefold() == "kick_2.caf" for i in plan.new)

    def test_a_manifest_that_double_booked_by_case_is_repaired(self, tmp_path):
        src = tmp_path / "s"
        a = touch(src / "packA/DropChords.wav")
        b = touch(src / "packB/Dropchords.wav")
        out = tmp_path / "out"
        out.mkdir()
        (out / "DropChords.caf").write_bytes(b"caff")

        manifest = sync.Manifest(tmp_path / "m.json")
        manifest.record(a, out / "DropChords.caf")
        manifest.record(b, out / "Dropchords.caf")   # same file on macOS

        plan = sync.plan(src, manifest, out)
        names = [i.output.name.casefold()
                 for i in plan.unchanged + plan.new + plan.changed]
        assert len(names) == len(set(names)), names
