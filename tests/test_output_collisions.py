"""Output filename collisions.

Two Splice packs both containing Kick_01.wav both convert to Kick_01.caf in a
flat output directory. The second silently replaced the first, so a bulk run
over a few thousand samples quietly lost files.
"""

from pathlib import Path

from convert_to_apple_loops import AppleLoopConverter


class TestResolveOutputPath:
    def test_an_unused_name_is_used_as_is(self, tmp_path):
        converter = AppleLoopConverter(output_dir=tmp_path)
        assert converter.resolve_output_path(Path("/a/Kick_01.wav")) == tmp_path / "Kick_01.caf"

    def test_a_taken_name_gets_a_suffix(self, tmp_path):
        (tmp_path / "Kick_01.caf").write_bytes(b"caff")
        converter = AppleLoopConverter(output_dir=tmp_path)
        assert converter.resolve_output_path(Path("/b/Kick_01.wav")) == tmp_path / "Kick_01_2.caf"

    def test_it_keeps_counting_past_the_second_collision(self, tmp_path):
        for name in ("Kick_01.caf", "Kick_01_2.caf"):
            (tmp_path / name).write_bytes(b"caff")
        converter = AppleLoopConverter(output_dir=tmp_path)
        assert converter.resolve_output_path(Path("/c/Kick_01.wav")) == tmp_path / "Kick_01_3.caf"

    def test_overwrite_mode_reuses_the_name(self, tmp_path):
        (tmp_path / "Kick_01.caf").write_bytes(b"caff")
        converter = AppleLoopConverter(output_dir=tmp_path, overwrite=True)
        assert converter.resolve_output_path(Path("/b/Kick_01.wav")) == tmp_path / "Kick_01.caf"

    def test_structure_preserving_output_keeps_the_source_folders(self, tmp_path):
        converter = AppleLoopConverter(output_dir=tmp_path)
        result = converter.resolve_output_path(
            Path("/lib/packs/deep/Kick_01.wav"), relative_to=Path("/lib"))
        assert result == tmp_path / "packs" / "deep" / "Kick_01.caf"
