"""A minimal VT screen model for OMH TUI rendering tests (test infrastructure, not shipped).

It interprets the subset of terminal output the TUI's screen layer emits:
printable text, CR, LF (scrolling the top row into `scrollback` at the
bottom), cursor up/down/forward/back, CUP, erase in line 0/1/2, erase in
display 0/1/2/3 (3 clears `scrollback`), SGR (recorded per cell), cursor
show/hide, the autowrap and bracketed-paste mode switches (recorded, never
acted on), the alternate screen (1049: the main screen and cursor are saved
on entry and restored on exit), and OSC strings (consumed, never painted,
their bodies kept in `osc`).
Any other CSI or two-byte escape is ignored. `resize` changes the size
the way xterm does without reflow.

Text never wraps. Writing past the right margin raises `VTOverflow` instead,
because the screen layer clips every row itself: a write that reaches the
margin with more to say is a clipping bug, and silently wrapping or
truncating it here would hide exactly that. As on xterm, the cursor stays on
the last column after a glyph lands there, so an erase-to-end issued at that
point deletes that glyph.

Glyph widths come from `omh.tui.width`, so this model agrees with the TUI by
construction; the width rules have their own tests with literal expectations.
"""

from __future__ import annotations

import re

from omh.tui.width import cluster_width, clusters, is_control

BLANK = " "
CONTINUATION = ""  # the right half of a 2-cell glyph

ESC = "\x1b"
OSC_INTRODUCER = "\x1b]"
BEL = "\x07"
STRING_TERMINATOR = "\x1b\\"
_CSI = re.compile(r"\x1b\[([0-?]*)([ -/]*)([@-~])")
_CSI_PREFIX = re.compile(r"\x1b\[[0-?]*[ -/]*\Z")
_EXTENDED_COLOUR = (38, 48)
_EXTENDED_LENGTH = {2: 5, 5: 3}  # 38;2;r;g;b and 38;5;n, counted from the 38


class VTOverflow(AssertionError):
    """Something was written past the right margin."""


class VTScreen:
    def __init__(self, rows: int = 24, cols: int = 80) -> None:
        self.rows = rows
        self.cols = cols
        self._text = [self._blank_row() for _ in range(rows)]
        self._attrs = [[() for _ in range(cols)] for _ in range(rows)]
        self.scrollback: list[str] = []
        self._row = 0
        self._col = 0
        self._at_margin = False
        self._sgr: tuple[int, ...] = ()
        self._pending = ""
        self.cursor_visible = True
        self.autowrap = True
        self.bracketed_paste = False
        self.osc: list[str] = []
        self._saved_main: tuple[list[list[str]], list[list[tuple[int, ...]]], int, int] | None = None

    @property
    def alternate(self) -> bool:
        """Whether the alternate screen is showing."""
        return self._saved_main is not None

    def resize(self, rows: int, cols: int) -> None:
        """Change the size as xterm does without reflow.

        A shorter screen first drops the rows below the cursor, then pushes rows
        off the top into scrollback until the cursor row fits; a taller one adds
        blank rows at the bottom. A narrower screen cuts every row (a 2-cell
        glyph cut in half is blanked), a wider one pads it.
        """
        overflow = max(self._row + 1 - rows, 0)
        for _ in range(overflow):
            self.scrollback.append(self.line(0))
            del self._text[0]
            del self._attrs[0]
        self._row -= overflow
        self._text = self._text[:rows]
        self._attrs = self._attrs[:rows]
        while len(self._text) < rows:
            self._text.append([BLANK] * self.cols)
            self._attrs.append([() for _ in range(self.cols)])
        for row in range(rows):
            if cols < self.cols:
                if self._text[row][cols] == CONTINUATION:
                    self._text[row][cols - 1] = BLANK
                self._text[row] = self._text[row][:cols]
                self._attrs[row] = self._attrs[row][:cols]
            else:
                self._text[row] += [BLANK] * (cols - self.cols)
                self._attrs[row] += [() for _ in range(cols - self.cols)]
        self.rows = rows
        self.cols = cols
        self._col = min(self._col, cols - 1)
        self._at_margin = False

    # -- reading the model ---------------------------------------------------

    @property
    def cursor(self) -> tuple[int, int]:
        return (self._row, self._col)

    def line(self, row: int) -> str:
        return "".join(cell for cell in self._text[row] if cell != CONTINUATION).rstrip(BLANK)

    def lines(self) -> list[str]:
        return [self.line(row) for row in range(self.rows)]

    def history(self) -> list[str]:
        """Scrollback followed by the screen, with trailing blank rows dropped."""
        everything = self.scrollback + self.lines()
        while everything and not everything[-1]:
            everything.pop()
        return everything

    def attrs_at(self, row: int, col: int) -> tuple[int, ...]:
        """The SGR parameters in force when the cell was last written or erased."""
        return self._attrs[row][col]

    # -- feeding output ------------------------------------------------------

    def feed(self, text: str) -> None:
        data = self._pending + text
        self._pending = ""
        index = 0
        while index < len(data):
            ch = data[index]
            if ch == ESC:
                consumed = self._escape(data, index)
                if consumed == 0:
                    self._pending = data[index:]
                    return
                index += consumed
                continue
            if is_control(ch):
                self._control(ch)
                index += 1
                continue
            end = index
            while end < len(data) and data[end] != ESC and not is_control(data[end]):
                end += 1
            for cluster in clusters(data[index:end]):
                self._put(cluster)
            index = end

    def _escape(self, data: str, index: int) -> int:
        if data.startswith(OSC_INTRODUCER, index):
            ends = [end for end in (data.find(BEL, index), data.find(STRING_TERMINATOR, index)) if end >= 0]
            if not ends:
                return 0  # incomplete; wait for the terminator
            end = min(ends)
            self.osc.append(data[index + len(OSC_INTRODUCER) : end])
            return end - index + (len(BEL) if data.startswith(BEL, end) else len(STRING_TERMINATOR))
        match = _CSI.match(data, index)
        if match is not None:
            self._csi(match.group(1), match.group(2), match.group(3))
            return match.end() - index
        if _CSI_PREFIX.match(data, index) or index + 1 >= len(data):
            return 0  # incomplete; wait for the rest
        return 2  # a two-byte escape this model does not interpret

    def _control(self, ch: str) -> None:
        if ch == "\r":
            self._col = 0
            self._at_margin = False
        elif ch == "\n":
            self._line_feed()
        elif ch == "\b":
            self._col = max(0, self._col - 1)
            self._at_margin = False

    def _line_feed(self) -> None:
        self._at_margin = False
        if self._row < self.rows - 1:
            self._row += 1
            return
        self.scrollback.append(self.line(0))
        del self._text[0]
        del self._attrs[0]
        self._text.append(self._blank_row())
        self._attrs.append([() for _ in range(self.cols)])

    def _put(self, cluster: str) -> None:
        width = cluster_width(cluster)
        if width == 0:
            if self._col > 0:  # a mark that combines onto the glyph before it
                self._text[self._row][self._col - 1] += cluster
            return
        if self._at_margin or self._col + width > self.cols:
            raise VTOverflow(
                f"write past the right margin at row {self._row}, col {self._col}: {cluster!r} "
                f"(width {width}, screen {self.cols} cols); row so far: {self.line(self._row)!r}"
            )
        row = self._text[self._row]
        self._break_wide_glyphs(self._row, self._col, self._col + width)
        row[self._col] = cluster
        self._attrs[self._row][self._col] = self._sgr
        if width == 2:
            row[self._col + 1] = CONTINUATION
            self._attrs[self._row][self._col + 1] = self._sgr
        if self._col + width >= self.cols:
            self._col = self.cols - 1
            self._at_margin = True
        else:
            self._col += width

    def _break_wide_glyphs(self, row: int, start: int, end: int) -> None:
        """Blank the other half of any 2-cell glyph that cells [start, end) cut through."""
        cells = self._text[row]
        if 0 < start < self.cols and cells[start] == CONTINUATION:
            cells[start - 1] = BLANK
        if end < self.cols and cells[end] == CONTINUATION:
            cells[end] = BLANK

    def _erase(self, row: int, start: int, end: int) -> None:
        self._break_wide_glyphs(row, start, end)
        for col in range(start, end):
            self._text[row][col] = BLANK
            self._attrs[row][col] = self._sgr

    def _blank_row(self) -> list[str]:
        return [BLANK] * self.cols

    # -- control sequences ---------------------------------------------------

    def _csi(self, parameters: str, intermediates: str, final: str) -> None:
        if intermediates:
            return
        if parameters.startswith("?"):
            self._private_mode(parameters[1:], final)
            return
        if final == "m":
            self._select_graphic_rendition(parameters)
            return
        numbers = [int(part) if part.isdigit() else 0 for part in parameters.split(";")] if parameters else []
        first = numbers[0] if numbers else 0
        count = max(first, 1)
        self._at_margin = False
        if final == "A":
            self._row = max(0, self._row - count)
        elif final == "B":
            self._row = min(self.rows - 1, self._row + count)
        elif final == "C":
            self._col = min(self.cols - 1, self._col + count)
        elif final == "D":
            self._col = max(0, self._col - count)
        elif final == "H":
            row = numbers[0] if numbers else 1
            col = numbers[1] if len(numbers) > 1 else 1
            self._row = min(max(row, 1), self.rows) - 1
            self._col = min(max(col, 1), self.cols) - 1
        elif final == "K":
            self._erase_in_line(first)
        elif final == "J":
            self._erase_in_display(first)
        elif final == "L":
            self._insert_lines(count)

    def _insert_lines(self, count: int) -> None:
        """IL with no scroll margins set: blank rows at the cursor row, the rows from it down moving down, those
        pushed past the screen's last row lost (not scrolled into scrollback); the cursor goes to column 0."""
        count = min(count, self.rows - self._row)
        for _ in range(count):
            self._text.insert(self._row, self._blank_row())
            self._attrs.insert(self._row, [() for _ in range(self.cols)])
        del self._text[self.rows :]
        del self._attrs[self.rows :]
        self._col = 0

    def _erase_in_line(self, mode: int) -> None:
        if mode == 0:
            self._erase(self._row, self._col, self.cols)
        elif mode == 1:
            self._erase(self._row, 0, self._col + 1)
        elif mode == 2:
            self._erase(self._row, 0, self.cols)

    def _erase_in_display(self, mode: int) -> None:
        if mode == 0:
            self._erase(self._row, self._col, self.cols)
            for row in range(self._row + 1, self.rows):
                self._erase(row, 0, self.cols)
        elif mode == 1:
            for row in range(self._row):
                self._erase(row, 0, self.cols)
            self._erase(self._row, 0, self._col + 1)
        elif mode == 2:
            for row in range(self.rows):
                self._erase(row, 0, self.cols)
        elif mode == 3:
            self.scrollback.clear()  # xterm: erase the saved lines

    def _private_mode(self, parameters: str, final: str) -> None:
        if final not in ("h", "l"):
            return
        enabled = final == "h"
        for parameter in parameters.split(";"):
            if parameter == "25":
                self.cursor_visible = enabled
            elif parameter == "7":
                self.autowrap = enabled
            elif parameter == "2004":
                self.bracketed_paste = enabled
            elif parameter == "1049":
                self._switch_screen(enabled)

    def _switch_screen(self, alternate: bool) -> None:
        if alternate and self._saved_main is None:
            self._saved_main = (self._text, self._attrs, self._row, self._col)
            self._text = [self._blank_row() for _ in range(self.rows)]
            self._attrs = [[() for _ in range(self.cols)] for _ in range(self.rows)]
        elif not alternate and self._saved_main is not None:
            self._text, self._attrs, self._row, self._col = self._saved_main
            self._saved_main = None
        self._at_margin = False

    def _select_graphic_rendition(self, parameters: str) -> None:
        values = [int(part) if part.isdigit() else 0 for part in parameters.split(";")] if parameters else [0]
        current = list(self._sgr)
        index = 0
        while index < len(values):
            value = values[index]
            # 38/48 carry their colour in the next 4 (truecolor) or 2 (256-colour)
            # values, whose zeros are channel values, not resets.
            if value in _EXTENDED_COLOUR and index + 1 < len(values) and values[index + 1] in _EXTENDED_LENGTH:
                length = _EXTENDED_LENGTH[values[index + 1]]
                current.extend(values[index : index + length])
                index += length
                continue
            if value == 0:
                current = []
            else:
                current.append(value)
            index += 1
        self._sgr = tuple(current)
