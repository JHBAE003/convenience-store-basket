"""Keep externally supplied spreadsheet cells literal without changing models.

Only export/display copies are sanitized. A visible text label is used instead
of relying on spreadsheet-specific apostrophe escaping that can disappear when
CSV files are saved and reopened.
"""
from __future__ import annotations

import unicodedata
from typing import Any


TEXT_PREFIX = '[텍스트] '
FORMULA_MARKERS = ('=', '+', '-', '@')


def csv_cell(value: Any) -> Any:
    """Preserve numeric values and label text that a sheet may parse as a formula.

Inspect leading whitespace/control/format characters and spreadsheet text
markers without stripping them from the original value. NFKC handles full-width
formula markers in locale-dependent spreadsheet parsers.
    """
    if value is None:
        return ''
    if not isinstance(value, str) or not value:
        return value
    probe = value
    for offset, character in enumerate(value):
        if character.isspace() or unicodedata.category(character) in ('Cc', 'Cf') or character == "'":
            continue
        probe = unicodedata.normalize('NFKC', character)
        break
    else:
        probe = ''
    if value.startswith(('\t', '\r', '\n')) or probe.startswith(FORMULA_MARKERS):
        return TEXT_PREFIX + value
    return value


def csv_safe_frame(frame):
    """Return a safe DataFrame copy for Streamlit's built-in CSV download.

Streamlit serializes the supplied values into its grid; its CSV toolbar has no
cell-sanitizer callback. Pass this copy to the grid and retain the original frame
for calculations. Numeric/missing values and original records are preserved.
    """
    result = frame.copy(deep=True).map(lambda value: csv_cell(value) if isinstance(value, str) else value)
    result.columns = result.columns.map(lambda value: csv_cell(value) if isinstance(value, str) else value)
    result.index = result.index.map(lambda value: csv_cell(value) if isinstance(value, str) else value)
    return result
