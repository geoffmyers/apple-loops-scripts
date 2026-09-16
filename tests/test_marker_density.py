"""Marker density should follow the audio, not a per-beat floor.

The detector used to force at least one marker per beat, appending an even
grid on top of the detected onsets whenever detection found fewer. Measured
against Apple's own library that was wrong twice over:

  - Apple ships 13.3% of its tempo-following loops with FEWER markers than
    beats, down to a single marker on a 16-beat sweep. Pads, risers and noise
    have nothing to slice, and Logic stretches them as one segment.
  - Appending a grid over real onsets put markers microseconds apart. On a
    real Splice library it fired on 11% of files and created a gap under 10 ms
    on 9% of them -- one drum loop went from 209 ms between markers to 0.18 ms,
    an 8-sample stretch segment.
"""

import numpy as np
import pytest

from convert_to_apple_loops import OnsetDetectionConfig, TransientDetector

SR = 44100


class TestNoPerBeatFloorByDefault:
    def test_a_sustained_pad_keeps_its_sparse_markers(self):
        detector = TransientDetector()
        onsets = np.array([0, 5 * SR])
        markers = detector._build_marker_list(
            onsets, num_frames=16 * SR, beat_count=16, min_markers=0)
        assert len(markers) == 3, markers

    def test_silence_yields_only_the_two_anchors(self):
        detector = TransientDetector()
        markers = detector._build_marker_list(
            np.array([]), num_frames=8 * SR, beat_count=16, min_markers=0)
        assert markers == [0, 8 * SR]


class TestAnOptInFloorStillWorks:
    def test_asking_for_a_floor_adds_markers(self):
        detector = TransientDetector()
        markers = detector._build_marker_list(
            np.array([0, 5 * SR]), num_frames=16 * SR, beat_count=16,
            min_markers=17)
        assert len(markers) >= 17

    def test_the_config_default_is_off(self):
        assert OnsetDetectionConfig().min_markers_per_beat == 0.0


class TestTheFloorNeverCrowdsARealTransient:
    """Even when asked for, a grid must not land microseconds from an onset."""

    def test_grid_points_too_close_to_an_onset_are_dropped(self):
        detector = TransientDetector()
        onset = int(4 * SR) + 200
        markers = detector._build_marker_list(
            np.array([onset]), num_frames=8 * SR, beat_count=16,
            min_markers=17, min_separation=int(0.03 * SR))

        gaps = [markers[i + 1] - markers[i] for i in range(len(markers) - 1)]
        assert min(gaps) >= int(0.03 * SR), min(gaps)

    def test_the_real_onset_is_the_one_that_survives(self):
        detector = TransientDetector()
        onset = int(4 * SR) + 200
        markers = detector._build_marker_list(
            np.array([onset]), num_frames=8 * SR, beat_count=16,
            min_markers=17, min_separation=int(0.03 * SR))
        assert onset in markers, "a detected transient must never be discarded"
