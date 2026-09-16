# Architecture

A small Python toolkit plus an optional SwiftUI wrapper.

## Layout

| Path | What lives there |
|---|---|
| `convert_to_apple_loops.py` | Audio/MIDI → Apple Loop CAF, writing the metadata chunks Logic reads. |
| `decode_apple_loops.py` | The inverse — reads metadata back out, with a `--verify` mode. |
| `apple_loops_vocabulary.py` | The allowed metadata values, checked against Apple's own shipped loops. |
| `splice_db.py` | Read-only reader for the Splice app's `sounds.db`, the metadata authority. |
| `scripts/` | The Splice→Logic sync, double-clickable launchers, and `validate-against-apple-loops.py`. |
| `macos-app/` | SwiftUI front end that shells out to the Python converter. |
| `tests/` | pytest suite, including ground-truth tests against real Apple loops. |

## The format facts that shape the code

- **Apple Loops store no tempo.** Logic derives it as
  `beat_count × 60 ÷ duration`, so a wrong beat count *is* a wrong tempo, and a
  fabricated duration is a fabricated tempo.
- **There is no one-shot flag.** A one-shot is the *absence* of a beat count and
  beat-markers chunk. Writing markers onto a single hit makes Logic stretch it.
- **Claims about the format are checked against Apple's shipped loops**, not
  against documentation — that is what `validate-against-apple-loops.py` is for,
  and it has corrected both the vocabulary and the verifier itself.
