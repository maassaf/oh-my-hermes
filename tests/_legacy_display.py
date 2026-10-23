"""What an install made before the display release left in a Hermes home.

OMH used to write `display.interface: tui`, `display.skin: omh` and three
collapsed `display.sections` into Hermes' config, and to install its status
widget and skin files under the Hermes home. Nothing in `src/` writes any of
that any more, so the migration tests rebuild it here: the writers below are
the consented branded-TUI writers as they shipped, and the seeders write the
files with the manifests the old installers recorded.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from omh.install.config_adapter import (
    ConfigChange,
    _canonical_display_sections,
    _display_edit_guard,
    _references_mapping_key,
    _scalar_value,
)
from omh.install.config_reversal import MANAGED_CONFIG_WRITES_STATE_KEY, managed_config_writes
from omh.skin_pack import MANIFEST_FILENAME as SKIN_MANIFEST_FILENAME
from omh.skin_pack import SKIN_THEMES, skin_payload
from omh.tui_widget_pack import MANIFEST_FILENAME as WIDGET_MANIFEST_FILENAME
from omh.tui_widget_pack import WIDGET_FILENAME

_COLLAPSED_DISPLAY_SECTIONS: tuple[tuple[str, str], ...] = (
    ("thinking", "collapsed"),
    ("tools", "collapsed"),
    ("subagents", "collapsed"),
)


def _activate_display_scalar(config_text: str, key: str, value: str) -> ConfigChange:
    """The old consented writer for one canonical `display` scalar."""
    lines = config_text.splitlines()
    guard = _display_edit_guard(lines)
    if guard:
        return ConfigChange(False, guard, config_text)
    if any(_references_mapping_key(line, f"display.{key}") for line in lines):
        return ConfigChange(False, f"dotted display.{key} is user-owned; leaving it alone", config_text)
    display_indices = [index for index, line in enumerate(lines) if line == "display:"]
    if not display_indices:
        text = (config_text.rstrip() + f"\n\ndisplay:\n  {key}: {value}\n").lstrip("\n")
        return ConfigChange(True, f"appended display.{key}", text)
    display_index = display_indices[0]
    entries: list[tuple[int, str]] = []
    key_like_lines = 0
    for index in range(display_index + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line.startswith(" "):
            break
        if _references_mapping_key(line, key):
            key_like_lines += 1
        if line.startswith("  ") and not line.startswith("    "):
            candidate, separator, rest = line.strip().partition(":")
            if separator and candidate == key:
                entries.append((index, rest.strip()))
    if key_like_lines != len(entries) or len(entries) > 1:
        return ConfigChange(False, f"ambiguous display.{key} is user-owned; leaving it alone", config_text)
    if entries:
        index, raw = entries[0]
        if not _scalar_value(raw) or raw.startswith(("{", "[", "|", ">")):
            return ConfigChange(False, f"non-scalar display.{key} is user-owned; leaving it alone", config_text)
        if _scalar_value(raw) == value:
            return ConfigChange(False, f"display.{key} is already {value}", config_text)
        lines[index] = f"  {key}: {value}"
        return ConfigChange(True, f"set display.{key} to {value}", "\n".join(lines) + "\n")
    lines.insert(display_index + 1, f"  {key}: {value}")
    return ConfigChange(True, f"inserted display.{key}", "\n".join(lines) + "\n")


def activate_tui_interface(config_text: str) -> ConfigChange:
    return _activate_display_scalar(config_text, "interface", "tui")


def activate_omh_skin(config_text: str, name: str = "omh") -> ConfigChange:
    return _activate_display_scalar(config_text, "skin", name)


def activate_display_sections(config_text: str) -> ConfigChange:
    """The old consented writer for the three collapsed sections, unset-only per key."""
    lines = config_text.splitlines()
    guard = _display_edit_guard(lines)
    if guard:
        return ConfigChange(False, guard, config_text)
    if any(_references_mapping_key(line, "display.sections") for line in lines):
        return ConfigChange(False, "dotted display.sections is user-owned; leaving it alone", config_text)
    located = _canonical_display_sections(lines)
    if isinstance(located, str):
        return ConfigChange(False, located, config_text)
    if located is None:
        block = ["  sections:", *(f"    {key}: {value}" for key, value in _COLLAPSED_DISPLAY_SECTIONS)]
        display_index = next((index for index, line in enumerate(lines) if line == "display:"), None)
        if display_index is None:
            text = (config_text.rstrip() + "\n\ndisplay:\n" + "\n".join(block) + "\n").lstrip("\n")
            return ConfigChange(True, "appended display.sections", text)
        lines[display_index + 1 : display_index + 1] = block
        return ConfigChange(True, "inserted display.sections", "\n".join(lines) + "\n")
    sections_index, children = located
    present = {key for _child_index, key, _raw in children}
    missing = [(key, value) for key, value in _COLLAPSED_DISPLAY_SECTIONS if key not in present]
    if not missing:
        return ConfigChange(False, "display.sections already sets every key", config_text)
    lines[sections_index + 1 : sections_index + 1] = [f"    {key}: {value}" for key, value in missing]
    return ConfigChange(True, "set display.sections", "\n".join(lines) + "\n")


def legacy_branded_display(config_text: str, skin: str = "omh") -> str:
    """The display keys an accepted branded-TUI prompt (or `--yes`) wrote, in the old order."""
    text = activate_tui_interface(config_text).text
    text = activate_omh_skin(text, skin).text
    return activate_display_sections(text).text


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# Stands in for the widget's bytes. Ownership is the manifest's digest of
# whatever is on disk, so any payload with its own digest recorded reads as
# OMH's, which is all the remover asks.
LEGACY_WIDGET_PAYLOAD = b"// omh-status.mjs as an earlier OMH installed it\nexport default function register() {}\n"


def seed_legacy_widget(hermes_home: Path) -> Path:
    """`omh-status.mjs` and its manifest, in the shape `install_tui_widget` wrote them."""
    destination = hermes_home / "tui-widgets" / WIDGET_FILENAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = LEGACY_WIDGET_PAYLOAD
    destination.write_bytes(payload)
    (destination.parent / WIDGET_MANIFEST_FILENAME).write_text(
        json.dumps(
            {"schema_version": "omh_tui_widget_manifest/v1", "filename": WIDGET_FILENAME, "sha256": _sha256(payload)},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return destination


def seed_legacy_skins(hermes_home: Path) -> Path:
    """Every shipped skin file and the v2 manifest, as `install_skin` wrote them."""
    skins = hermes_home / "skins"
    skins.mkdir(parents=True, exist_ok=True)
    records: dict[str, dict[str, str]] = {}
    for theme in SKIN_THEMES:
        payload = skin_payload(theme.skin_name)
        (skins / theme.filename).write_bytes(payload)
        records[theme.filename] = {"sha256": _sha256(payload)}
    (skins / SKIN_MANIFEST_FILENAME).write_text(
        json.dumps({"schema_version": "omh_skin_manifest/v2", "files": records}, sort_keys=True),
        encoding="utf-8",
    )
    return skins


def brand_legacy_home(omh_home: Path, config: Path, skin: str = "omh") -> None:
    """Rewrite one Hermes home the way the old version left it after today's setup ran there.

    The branded display keys go into the config, the write record names them
    (and loses the release marker today's setup recorded), and the widget and
    skin files go in beside the config with their manifests.

    Setup only ever wrote `omh`; any other `skin` is a theme `omh theme use`
    picked afterwards, which rewrote `display.skin` and left the record
    naming `omh`, the shape the v3.0.2 fixture recorded from a real run.
    """
    current = config.read_text(encoding="utf-8")
    branded = legacy_branded_display(current)
    state_path = omh_home / "runtime" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    stored = state[MANAGED_CONFIG_WRITES_STATE_KEY]
    key = str(config.resolve())
    stored[key].pop("display_released_at", None)
    state[MANAGED_CONFIG_WRITES_STATE_KEY] = managed_config_writes(
        current, branded, config_path=key, previous=stored, now="2026-10-01T00:00:00Z"
    )
    state_path.write_text(json.dumps(state), encoding="utf-8", newline="")
    if skin != "omh":
        branded = activate_omh_skin(branded, skin).text
    config.write_text(branded, encoding="utf-8", newline="")
    seed_legacy_widget(config.parent)
    seed_legacy_skins(config.parent)
