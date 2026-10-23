"""Plain text out of the OMH TUI's styled lines, for assertions in the TUI tests."""

from __future__ import annotations

from collections.abc import Iterable

from omh.tui.frame import INFO_INSET as INFO_INSET_CELLS, RULE
from omh.tui.style import Line

INFO_INSET = " " * INFO_INSET_CELLS  # the info line under the composer's rule starts this far in


def line_text(line: Line) -> str:
    """The characters one styled line shows, styles dropped."""
    return "".join(span.text for span in line)


def lines_text(lines: Iterable[Line]) -> list[str]:
    """`line_text` of each line, in order."""
    return [line_text(line) for line in lines]


def composer_text(row: str) -> str:
    """A composer row as typed (the composer has no sides): trailing blanks dropped."""
    return row.rstrip()


def composer_row(text: str, cols: int) -> str:
    """The screen text of a composer row holding `text` at `cols` columns: the row itself, since the composer has no
    sides to pad to (a screen row's trailing blanks are dropped)."""
    return text[:cols]


def rule_edges(cols: int) -> tuple[str, str]:
    """The screen text of the composer's rules, above and under it, at `cols` columns."""
    return RULE * cols, RULE * cols


RULE_ROW = RULE * 2  # how one of the composer's rules starts on the screen: no other row starts with it


def rule_rows(rows: Iterable[str]) -> list[int]:
    """The indexes of the composer's rules among screen rows: its top rule first, then its bottom one."""
    return [index for index, row in enumerate(rows) if row.startswith(RULE_ROW)]
