"""The model picker: `/model` alone lists the models Hermes offers this session, filtered as you type.

`model.options {session_id}` answers with the providers Hermes can reach,
each with the models it lists, and the session's current model and provider.
The picker shows one row per provider and model, the current one marked and
highlighted (a model Hermes runs but no provider lists, such as a route the
provider's own list leaves out, comes first, so the session's model is always
a row), the position of the highlight once the list scrolls (`Model
(12/162)`), and returns the one picked; its last row is the filter
(`session_picker.filter_line`), where the composer is drawn while no picker is
open. It holds no IO: the app sends the
pick as Hermes' own `/model <name> --provider <slug> --session` through
`slash.exec`, so the switch is Hermes' (refused while a turn runs) and lasts
for this session only. A name or slug that is not one plain word
(whitespace, a leading dash of any kind, a control character) is left out,
so a pick can never add a flag such as `--global` to that command: Hermes
splits the arguments on whitespace and reads a token that starts with `--`,
or with one of the dashes U+2012 to U+2015 before a flag word, as a flag.

Keys: Up/Down move one row, PageUp/PageDown one page; printable text filters
(model or provider, ignoring case) and Backspace takes a filter character
back; Enter picks the highlighted row; Esc or Ctrl+C closes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import unicodedata

from .escapes import strip_escapes
from .keys import BACKSPACE, CHAR, DOWN, ENTER, ESCAPE, PAGE_DOWN, PAGE_UP, UP, Key, Paste
from .session_picker import picker_frame
from .style import Line, Span
from .theme import Theme
from .width import cell_width, truncate, truncate_line

PICK = "pick"
CLOSE = "close"
# Its keys, longest first: on the info line while it is open.
KEYS = ("↑↓ move · enter switches this session · esc closes", "↑↓ enter switches · esc closes", "esc closes")
TITLE = " Model ({count})"
TITLE_AT = " Model ({at}/{count})"  # once the list has more rows than it shows
CURRENT_MARK = "●"


@dataclass(frozen=True)
class ModelRow:
    model: str
    provider: str  # the slug `--provider` takes
    provider_name: str
    current: bool

    @property
    def arg(self) -> str:
        """What Hermes' own `/model` takes to switch this session, and only this session, to this row."""
        return f"{self.model} --provider {self.provider} --session"


@dataclass(frozen=True)
class ModelAction:
    kind: str  # PICK or CLOSE
    row: ModelRow | None = None


def _word(value: object) -> str:
    """`value` when it is one plain word a command line carries as one argument; "" otherwise.

    It may not start with a dash of any kind (Unicode category Pd, `-` and the en and em dashes among them).
    """
    if not isinstance(value, str) or not value or unicodedata.category(value[0]) == "Pd":
        return ""
    return value if all(char.isprintable() and not char.isspace() for char in value) else ""


def _name(value: object, fallback: str) -> str:
    text = "".join(char for char in strip_escapes(value) if char.isprintable()) if isinstance(value, str) else ""
    return text.strip() or fallback


def model_rows(result: Mapping[str, object]) -> list[ModelRow]:
    """Every provider's models in a `model.options` answer, in Hermes' order; a malformed or unsafe one is skipped.

    A row is current when its model is the session's and its provider is the
    session's, by slug, by one of the provider's `aliases`, or as the one
    Hermes marks `is_current`. When no row is, the session's model and
    provider come first as a row of their own, if both are plain words.
    """
    model, provider = result.get("model"), result.get("provider")
    providers = result.get("providers")
    rows: list[ModelRow] = []
    for entry in providers if isinstance(providers, list) else []:
        if not isinstance(entry, dict):
            continue
        slug, models = _word(entry.get("slug")), entry.get("models")
        if not slug or not isinstance(models, list):
            continue
        name = _name(entry.get("name"), slug)
        aliases = entry.get("aliases")
        current_provider = (
            slug == provider or entry.get("is_current") is True
            or (isinstance(aliases, list) and isinstance(provider, str) and provider in aliases)
        )
        for listed in map(_word, models):
            if listed:
                rows.append(ModelRow(listed, slug, name, current_provider and listed == model))
    running, running_provider = _word(model), _word(provider)
    if running and running_provider and not any(row.current for row in rows):
        name = next((row.provider_name for row in rows if row.provider == running_provider), running_provider)
        rows.insert(0, ModelRow(running, running_provider, name, True))
    return rows


@dataclass
class ModelPicker:
    rows: list[ModelRow]
    current: str  # the session's model, named in the header whether or not a row holds it
    query: str = ""
    index: int = -1  # -1 picks the current row, else the first
    page: int = 1  # rows the last render showed; PageUp/PageDown move by this many
    caret: tuple[int, int] | None = None  # the last render's caret, at the end of its filter row

    def __post_init__(self) -> None:
        if self.index < 0:
            self.index = next((number for number, row in enumerate(self.rows) if row.current), 0)

    def visible(self) -> list[ModelRow]:
        needle = self.query.casefold()
        if not needle:
            return list(self.rows)
        return [row for row in self.rows if needle in f"{row.model}\n{row.provider}\n{row.provider_name}".casefold()]

    def handle_key(self, event: Key | Paste) -> ModelAction | None:
        """Apply one key; a `ModelAction` when the person picked a model or closed the picker."""
        if isinstance(event, Paste):
            self._filter(self.query + event.text.split("\n", 1)[0])
            return None
        if event.name == ESCAPE or (event.name == CHAR and event.ctrl and event.char == "c"):
            return ModelAction(CLOSE)
        shown = self.visible()
        if event.name == ENTER:
            return ModelAction(PICK, shown[min(self.index, len(shown) - 1)]) if shown else None
        steps = {UP: -1, DOWN: 1, PAGE_UP: -self.page, PAGE_DOWN: self.page}
        if event.name in steps and shown:
            self.index = min(max(self.index + steps[event.name], 0), len(shown) - 1)
        elif event.name == BACKSPACE:
            self._filter(self.query[:-1])
        elif event.name == CHAR and not event.ctrl and not event.alt:
            self._filter(self.query + event.char)
        return None

    def _filter(self, query: str) -> None:
        self.query = query
        self.index = 0

    def lines(self, width: int, theme: Theme, max_rows: int) -> list[Line]:
        """A header row naming the current model, and the highlight's place once the rows scroll, the filtered
        rows scrolled so the highlighted one shows, the filter row."""
        shown = self.visible()
        self.index = min(self.index, max(len(shown) - 1, 0))
        scrolls = max_rows > 2 and len(shown) > max_rows - 2  # the header and the filter row take two
        title = TITLE_AT.format(at=self.index + 1, count=len(shown)) if scrolls else TITLE.format(count=len(self.rows))
        head = (
            Span(title, theme.heading),
            Span(f"  now {self.current}" if self.current else "", theme.text),
        )
        frame = picker_frame(
            head, len(shown), self.index,
            lambda number: self._row(shown[number], number == self.index, width, theme),
            " no model matches the filter; /model <name> --provider <slug> names any", self.query, width, theme, max_rows,
        )
        self.page, self.caret = frame.page, frame.caret
        return frame.lines

    def _row(self, row: ModelRow, highlighted: bool, width: int, theme: Theme) -> Line:
        style = theme.menu_current if highlighted else theme.menu
        lead = f" {CURRENT_MARK if row.current else ' '} "
        meta = f" {row.provider_name} "
        room = width - cell_width(lead) - cell_width(meta)
        if room < 8:
            return truncate_line((Span(lead + row.model, style),), width)
        label = truncate(row.model, room)
        return (Span(lead + label + " " * (room - cell_width(label)) + meta, style),)
