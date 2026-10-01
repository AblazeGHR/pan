'''Unit tests for cbc rewind scope selection (confirmation menu option 1/2/3).'''

from __future__ import annotations

import pytest

from packages.core.rewind.driver import (
    REWIND_SCOPE_LABELS,
    coerce_rewind_scope,
    selected_option_matches_scope,
)


def _screen(selected: int) -> str:
    labels = [REWIND_SCOPE_LABELS[i] for i in (1, 2, 3)]
    lines = ['How should we rewind?']
    for index, label in enumerate(labels, start=1):
        marker = chr(0x276F) if index == selected else ' '
        lines.append(f'{marker} {index}. {label}')
    lines.append('  Never Mind')
    return chr(10).join(lines)


@pytest.mark.parametrize('value,expected', [
    (1, 1), (2, 2), (3, 3), ('1', 1), ('2', 2), ('3', 3),
])
def test_coerce_rewind_scope_accepts_valid(value, expected):
    assert coerce_rewind_scope(value) == expected


@pytest.mark.parametrize('value', [0, 4, -1, 'x', '2x', None, True, False, 1.5])
def test_coerce_rewind_scope_rejects_invalid(value):
    with pytest.raises(ValueError):
        coerce_rewind_scope(value)


@pytest.mark.parametrize('selected', (1, 2, 3))
def test_selected_option_matches_only_its_scope(selected):
    screen = _screen(selected)
    for scope in (1, 2, 3):
        assert selected_option_matches_scope(screen, scope) is (scope == selected)


def test_selected_option_requires_highlight_marker():
    screen = 'Restore code and conversation\nRestore conversation\nRestore code'
    assert selected_option_matches_scope(screen, 1) is False
    assert selected_option_matches_scope(screen, 2) is False
    assert selected_option_matches_scope(screen, 3) is False
