"""Locating Splice's database without being told where it is.

The real path on macOS nests the database under a per-account directory:
    ~/Library/Application Support/com.splice.Splice/users/default/user-<id>/sounds.db
A fixed list of paths cannot match that, so `--splice-db` with no value found
nothing on the one machine it was meant to work on.
"""

from pathlib import Path

import splice_db


class TestFindSoundsDb:
    def test_finds_the_database_under_a_per_account_directory(self, tmp_path, monkeypatch):
        real = (tmp_path / "Library/Application Support/com.splice.Splice"
                / "users/default/user-4116493293")
        real.mkdir(parents=True)
        (real / "sounds.db").write_bytes(b"SQLite format 3\x00")

        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert splice_db.find_sounds_db() == real / "sounds.db"

    def test_returns_none_when_there_is_no_database(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert splice_db.find_sounds_db() is None

    def test_prefers_the_most_recently_modified_when_several_accounts_exist(
            self, tmp_path, monkeypatch):
        base = tmp_path / "Library/Application Support/com.splice.Splice/users/default"
        for name, mtime in (("user-1", 1_000_000), ("user-2", 2_000_000)):
            d = base / name
            d.mkdir(parents=True)
            db = d / "sounds.db"
            db.write_bytes(b"SQLite format 3\x00")
            import os
            os.utime(db, (mtime, mtime))

        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        found = splice_db.find_sounds_db()
        assert found is not None and found.parent.name == "user-2"
