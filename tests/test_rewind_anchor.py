'''Anchor matching / navigation tests for cbc rewind (Bug 1 regression).

Ground truth from evidence/rewind_bugfix_anchor_probe.json (2026-09-30) and
evidence/rewind_bugfix2_keymap.json (2026-09-30):
  - the checkpoint preview shows only the FIRST LINE of a message,
    hard-truncated to one screen row;
  - relative timestamps ("37s ago") tick every second, so whole-screen
    diffs never go quiet at the top of the list;
  - the settled selection starts on the TOP (oldest) checkpoint — the
    initial "(current)" highlight is a transient opening state;
  - ArrowDown walks one step toward newer entries, ArrowUp toward older
    ones, and keypresses sent without waiting for the re-render are
    swallowed, so every step is settle-verified.
'''

from __future__ import annotations

import pytest

from packages.core.rewind.driver import (
    AnchorOutOfRangeError,
    _anchor_terms,
    navigate_to_anchor,
    selected_checkpoint_matches_anchor,
)

ENTRIES = [
    'PAN_WRAP_ANCHOR please handle the following task lorem ipsum dolor sit amet consectetur adip…',
    'PAN_ANCHOR_MULTI_FIRST',
    'Reply with exactly PAN_REWIND_FORK_READY.',
]


def menu_screen(selected: int, tick: int = 0, entries: list[str] | None = None) -> str:
    entries = entries if entries is not None else ENTRIES
    rows = ['Restore and fork the conversation to a checkpoint:', '']
    for index, entry in enumerate(entries):
        marker = chr(0x276F) if index == selected else ' '
        rows.append(f'{marker} {entry}')
        rows.append(f'  No code changes · {10 + tick}s ago')
    current_marker = chr(0x276F) if selected == len(entries) else ' '
    rows.append(f'{current_marker} (current)')
    return '\n'.join(rows)


class FakeSession:
    '''Live-menu fake: ArrowUp moves toward older entries (lower index),
    ArrowDown toward newer ones; both clamp at the list ends. The selection
    starts on the TOP entry, matching the settled real menu.'''

    def __init__(self, entry_count: int, selected: int = 0,
                 entries: list[str] | None = None):
        # entry_count excludes the trailing "(current)" pseudo-entry.
        self.entry_count = entry_count
        self.entries = entries
        self.selected = selected
        self.sent: list[str] = []

    def text(self) -> str:
        return menu_screen(self.selected, entries=self.entries)

    def send(self, value: str) -> None:
        self.sent.append(value)
        if value == chr(27) + '[A':
            self.selected = max(0, self.selected - 1)
        elif value == chr(27) + '[B':
            self.selected = min(self.entry_count, self.selected + 1)

    def wait_for(self, predicate, deadline):
        return predicate(self.text()), self.text()


MULTI_ANCHOR = (
    'PAN_ANCHOR_MULTI_FIRST\n'
    'second line with PAN_ANCHOR_MULTI_SECOND marker\n'
    'third line'
)


def test_anchor_terms_cover_first_line_for_multiline_messages():
    terms = _anchor_terms(MULTI_ANCHOR + ' ' + 'extra ' * 30)
    assert any('pan_anchor_multi_first' == term for term in terms)


def test_single_line_long_anchor_keeps_two_terms():
    anchor = 'prefix ' + 'middle ' * 40 + 'suffix-marker'
    assert len(_anchor_terms(anchor)) == 2


def test_selected_checkpoint_matches_first_line_preview():
    screen = menu_screen(selected=1)
    assert selected_checkpoint_matches_anchor(screen, MULTI_ANCHOR)
    assert not selected_checkpoint_matches_anchor(menu_screen(selected=0), MULTI_ANCHOR)


def test_navigate_finds_multiline_anchor():
    # Selection starts at the top (index 0); the anchor is entry 1, so one
    # verified ArrowDown lands on it.
    session = FakeSession(len(ENTRIES), selected=0)
    steps, _ = navigate_to_anchor(session, MULTI_ANCHOR)
    assert steps == 1  # one verified ArrowDown from the top
    assert session.sent == [chr(27) + '[A', chr(27) + '[B']


def test_navigate_stops_at_list_bottom_despite_ticking_timestamps():
    # A missing anchor walks the whole list and stops at "(current)"
    # instead of burning max_steps.
    session = FakeSession(len(ENTRIES), selected=0)
    with pytest.raises(AnchorOutOfRangeError, match='不在可回滚的检查点范围内'):
        navigate_to_anchor(session, 'PAN_DEFINITELY_MISSING_ANCHOR zzz')
    # 1 probe Up + walks to fork-prompt (2) and (current) (3) + 1 no-op Down.
    assert len(session.sent) == 5


def test_navigate_without_selection_marker_is_a_matching_error():
    class StaticSession:
        def text(self) -> str:
            return 'no checkpoint list here'

        def send(self, value: str) -> None:
            pass

        def wait_for(self, predicate, deadline):
            return predicate(self.text()), self.text()

    session = StaticSession()
    with pytest.raises(LookupError, match='无法识别检查点列表') as exc_info:
        navigate_to_anchor(session, 'some anchor text')
    assert type(exc_info.value) is LookupError
    assert not isinstance(exc_info.value, AnchorOutOfRangeError)


def test_navigate_match_ordinal_selects_the_requested_occurrence():
    # Two checkpoints share the same first-line preview (worker reports do);
    # match_ordinal picks the exact occurrence instead of the first match.
    entries = [
        'worker one header shared by both reports here',
        'unrelated middle checkpoint about something else entirely',
        'worker one header shared by both reports here',
    ]
    session = FakeSession(len(entries), selected=0, entries=entries)
    steps, screen = navigate_to_anchor(session, entries[2], match_ordinal=1)
    assert steps == 2
    assert session.sent == [chr(27) + '[A', chr(27) + '[B', chr(27) + '[B']
    assert selected_checkpoint_matches_anchor(screen, entries[2])


def test_navigate_match_ordinal_out_of_range_is_a_clear_error():
    entries = [
        'worker one header shared by both reports here',
        'unrelated middle checkpoint about something else entirely',
    ]
    session = FakeSession(len(entries), selected=0, entries=entries)
    with pytest.raises(AnchorOutOfRangeError, match='匹配序号超出范围'):
        navigate_to_anchor(session, entries[0], match_ordinal=5)


def test_compute_match_ordinal_counts_prior_first_line_collisions():
    from packages.core.rewind.driver import compute_match_ordinal
    history = [
        {'role': 'user', 'content': 'shared header\nfirst body'},
        {'role': 'assistant', 'content': 'shared header\nassistant copy'},
        {'role': 'user', 'content': 'unrelated message'},
        {'role': 'user', 'content': 'shared header\nsecond body'},
        {'role': 'user', 'content': 'shared header\nthird body'},
    ]
    # Anchor is index 4; two prior user messages share its first line.
    assert compute_match_ordinal(history, 4, 'shared header\nthird body') == 2
    assert compute_match_ordinal(history, 2, 'unrelated message') == 0
