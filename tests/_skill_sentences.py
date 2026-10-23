"""Split rendered skill text into sentences for the native-surface sentence gates.

One copy for the modules that check a body sentence by sentence
(`test_operator_native_gates`, `test_memory_learning_native_loop`,
`test_production_native_producers`, `test_tracker_native_surfaces`), so a
change to where a sentence ends reaches all of them at once.
"""

from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def sentences(content: str) -> list[str]:
    """Each line's sentences: a break after `.`, `!`, or `?` and before a capital letter."""
    return [sentence for line in content.splitlines() for sentence in _SENTENCE_END.split(line)]
