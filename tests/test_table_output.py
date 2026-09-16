"""The conversion table.

The dry-run table used to be a separate hardcoded column list plus a
`row[:-2]` slice, so inserting a column silently misaligned every heading.
Columns and rows are built from the same place now, and this asserts it.
"""

from convert_to_apple_loops import (
    LoopMetadata,
    get_convert_table_columns,
    metadata_to_table_row,
)


class TestColumnRowAlignment:
    def test_a_full_row_has_one_value_per_column(self):
        columns = get_convert_table_columns()
        row = metadata_to_table_row("x.wav", LoopMetadata(), markers=0, status="OK")
        assert len(row) == len(columns)

    def test_a_dry_run_row_has_one_value_per_dry_run_column(self):
        columns = get_convert_table_columns(include_status=False)
        row = metadata_to_table_row("x.wav", LoopMetadata(), include_status=False)
        assert len(row) == len(columns)


class TestRowContent:
    def test_a_loop_is_labelled_loop(self):
        row = metadata_to_table_row("x.wav", LoopMetadata(beat_count=16))
        assert "Loop" in row

    def test_a_one_shot_is_labelled_distinctly(self):
        row = metadata_to_table_row("x.wav", LoopMetadata(is_one_shot=True))
        assert "1-Shot" in row

    def test_the_metadata_source_is_shown(self):
        row = metadata_to_table_row(
            "x.wav", LoopMetadata(metadata_source="splice"))
        assert "splice" in row
