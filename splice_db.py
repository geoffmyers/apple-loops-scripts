"""Read the Splice desktop app's local sample database.

The Splice app keeps a SQLite database, ``sounds.db``, holding one row per
sample it has ever downloaded. That row already carries the sample's real BPM,
key, genre, tag list and -- critically -- whether it is a loop or a one-shot.

Every one of those is otherwise guessed from the filename, and a filename is a
guess: a wrong BPM writes a wrong beat count, and a one-shot mistaken for a
loop gets beat markers and is time-stretched by Logic.

The `samples` table columns, as shipped::

    id, local_path, attr_hash, dir, audio_key, bpm, chord_type, duration,
    file_hash, sas_id, filename, genre, pack_uuid, sample_type, tags,
    popularity, purchased_at, last_modified_at, waveform_url, provider_name

This module stays deliberately free of Apple Loops concepts. It reports what
Splice recorded; mapping that onto Apple's category and genre vocabulary is the
converter's job.
"""

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

# Where the Splice app keeps its database, and where an exported log drop puts
# a copy. The exported copy is the documented route: Splice app -> Settings ->
# Download logs, then users/default/<username>/sounds.db inside the zip.
DEFAULT_DB_LOCATIONS = (
    "~/Library/Application Support/com.splice.Splice/sounds.db",
    "~/Library/Application Support/Splice/sounds.db",
    "~/.config/Splice/sounds.db",
    "~/Splice/sounds.db",
)

# The live database is nested under a per-account directory named for the
# Splice user id, so it can only be found by globbing.
DEFAULT_DB_GLOBS = (
    "Library/Application Support/com.splice.Splice/users/*/*/sounds.db",
    "Library/Application Support/Splice/users/*/*/sounds.db",
)

# Note letter, optional accidental, optional scale indicator. Splice writes the
# key either as a bare note with the scale in `chord_type`, or as a note with
# the scale glued on ("Cm", "Bbm", "F#maj").
_KEY_RE = re.compile(r'^([A-Ga-g])([b#]?)(m|min|minor|maj|major|M)?$')

# Every multi-character scale token is matched case-insensitively; a bare
# single letter is the one exception ('M' = major, 'm' = minor, a common
# chord-notation convention) and is handled separately in
# `_normalise_scale()` since lower-casing 'M' would collide with 'm'.
_SCALE_TOKENS = {
    'maj': 'major',
    'major': 'major',
    'min': 'minor',
    'minor': 'minor',
}

# Splice's own word for a rhythmic phrase. Anything else -- including a NULL,
# which is common -- is treated as a one-shot, because assuming "loop" is the
# damaging direction: it puts beat markers on a single hit.
LOOP_SAMPLE_TYPE = 'loop'


def parse_splice_key(audio_key: Optional[str],
                     chord_type: Optional[str]) -> Tuple[str, str]:
    """Normalise a Splice key into an (Apple key signature, key type) pair.

    Returns ``('', '')`` when Splice recorded no usable key.

    The scale is resolved in order of how directly it was stated: the dedicated
    ``chord_type`` column first, then a suffix glued onto the key itself. A
    bare note with no scale anywhere reports ``'both'`` rather than guessing --
    key type drives Loop Browser filtering, not transposition, so an honest
    "either" costs nothing while a wrong guess mis-files the sample.
    """
    raw_key = (audio_key or '').strip()
    if not raw_key:
        return '', ''

    match = _KEY_RE.match(raw_key)
    if not match:
        return '', ''

    note = match.group(1).upper() + match.group(2)
    suffix = match.group(3)

    scale = _normalise_scale(chord_type) or _normalise_scale(suffix) or 'both'
    return note, scale


def _normalise_scale(token: Optional[str]) -> str:
    """Map a scale token onto Apple's key type vocabulary, or '' if unknown."""
    if not token:
        return ''
    stripped = token.strip()
    if stripped == 'M':
        return 'major'
    if stripped == 'm':
        return 'minor'
    return _SCALE_TOKENS.get(stripped.lower(), '')


@dataclass
class SpliceSample:
    """One row of the Splice `samples` table."""

    filename: str = ''
    local_path: str = ''
    bpm: Optional[float] = None
    audio_key: str = ''
    chord_type: str = ''
    genre: str = ''
    sample_type: str = ''
    tags: str = ''
    duration: Optional[float] = None
    pack_uuid: str = ''
    provider_name: str = ''

    @property
    def is_loop(self) -> bool:
        """True only when Splice explicitly called this a loop."""
        return (self.sample_type or '').strip().lower() == LOOP_SAMPLE_TYPE

    @property
    def tag_list(self) -> List[str]:
        """Tags lowercased and split, most specific first (Splice's order)."""
        if not self.tags:
            return []
        return [t.strip().lower() for t in self.tags.split(',') if t.strip()]

    @property
    def key_signature(self) -> str:
        return parse_splice_key(self.audio_key, self.chord_type)[0]

    @property
    def key_type(self) -> str:
        return parse_splice_key(self.audio_key, self.chord_type)[1]


class SpliceLibrary:
    """Read-only view over a Splice ``sounds.db``."""

    #: Row fields this reader consumes. Splice has added columns over time, so
    #: only these are required to be present.
    REQUIRED_COLUMNS = ('filename', 'local_path')

    def __init__(self, db_path):
        self.db_path = Path(db_path).expanduser()
        if not self.db_path.exists():
            raise FileNotFoundError(f"Splice database not found: {self.db_path}")

        self._conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row
        self._verify_schema()
        self._by_basename: Optional[Dict[str, SpliceSample]] = None

    def _verify_schema(self) -> None:
        tables = {
            row[0] for row in
            self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if 'samples' not in tables:
            raise ValueError(
                f"{self.db_path} has no 'samples' table -- is this a Splice "
                f"sounds.db? Found tables: {sorted(tables) or 'none'}"
            )

        columns = {
            row['name'] for row in
            self._conn.execute("PRAGMA table_info(samples)")
        }
        missing = [c for c in self.REQUIRED_COLUMNS if c not in columns]
        if missing:
            raise ValueError(
                f"{self.db_path} 'samples' table is missing required "
                f"column(s): {', '.join(missing)}"
            )
        self._columns = columns

    def samples(self) -> Iterator[SpliceSample]:
        """Every sample that has actually been downloaded to this machine."""
        rows = self._conn.execute(
            "SELECT * FROM samples WHERE local_path IS NOT NULL AND local_path != ''"
        )
        for row in rows:
            yield self._row_to_sample(row)

    def _row_to_sample(self, row: sqlite3.Row) -> SpliceSample:
        def get(name, default=''):
            if name not in self._columns:
                return default
            value = row[name]
            return default if value is None else value

        return SpliceSample(
            filename=get('filename'),
            local_path=get('local_path'),
            bpm=get('bpm', None),
            audio_key=get('audio_key'),
            chord_type=get('chord_type'),
            genre=get('genre'),
            sample_type=get('sample_type'),
            tags=get('tags'),
            duration=get('duration', None),
            pack_uuid=get('pack_uuid'),
            provider_name=get('provider_name'),
        )

    def lookup(self, path) -> Optional[SpliceSample]:
        """Find a sample by basename.

        Matching on basename rather than full path is deliberate: people move
        and re-organise their Splice folder, and the `local_path` recorded at
        download time then points at nothing while the file sits there under a
        name that still matches.
        """
        if self._by_basename is None:
            self._by_basename = {}
            for sample in self.samples():
                key = _basename_key(sample.filename or sample.local_path)
                # First row wins; Splice can hold several rows per filename.
                self._by_basename.setdefault(key, sample)

        return self._by_basename.get(_basename_key(path))

    def __len__(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM samples WHERE local_path IS NOT NULL AND local_path != ''"
        ).fetchone()
        return row[0]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _basename_key(path) -> str:
    """Case-folded basename, used as the lookup key."""
    return Path(str(path)).name.casefold()


def find_sounds_db() -> Optional[Path]:
    """Locate a Splice sounds.db, or None.

    The live database sits under a per-account directory whose name contains
    the Splice user id, so the search has to glob rather than test fixed paths:

        ~/Library/Application Support/com.splice.Splice/users/<profile>/<account>/sounds.db

    Where several accounts have signed in on one machine, the most recently
    modified database is the one in use.
    """
    home = Path.home()
    found = []

    for pattern in DEFAULT_DB_GLOBS:
        for path in home.glob(pattern):
            if path.is_file():
                found.append(path)

    for candidate in DEFAULT_DB_LOCATIONS:
        path = Path(candidate).expanduser()
        if path.is_file():
            found.append(path)

    if not found:
        return None

    return max(found, key=lambda p: p.stat().st_mtime)
