"""Unit tests for the rules-injection typing guard.

_typing_state_after_key is a pure state machine — no GTK required.
It protects against rules injection firing while the user is composing
a message (pauses mid-sentence for > _IDLE_QUIET_SEC seconds).
"""
import pytest

from bterminal.ui.terminal_tab import (
    TerminalTab,
    _TYPING_CTRL_ABORT_KEYS,
    _TYPING_PURE_MODIFIER_KEYS,
    _TYPING_SUBMIT_KEYS,
)

# Raw GDK keysym values (no gi import needed)
KEY_Return    = 0xff0d
KEY_KP_Enter  = 0xff8d
KEY_Escape    = 0xff1b
KEY_a         = 0x0061
KEY_z         = 0x007a
KEY_Space     = 0x0020
KEY_BackSpace = 0xff08
KEY_c         = 0x0063
KEY_C         = 0x0043
KEY_d         = 0x0064
KEY_D         = 0x0044
KEY_Shift_L   = 0xffe1
KEY_Shift_R   = 0xffe2
KEY_Control_L = 0xffe3
KEY_Alt_L     = 0xffe9
KEY_Super_L   = 0xffeb
KEY_Caps_Lock = 0xffe5


def key(is_typing, keyval, ctrl=False):
    return TerminalTab._typing_state_after_key(is_typing, keyval, ctrl)


# ── constant sanity checks ──────────────────────────────────────────────────

def test_submit_keys_contain_expected():
    assert KEY_Return   in _TYPING_SUBMIT_KEYS
    assert KEY_KP_Enter in _TYPING_SUBMIT_KEYS
    assert KEY_Escape   in _TYPING_SUBMIT_KEYS


def test_ctrl_abort_keys_contain_expected():
    assert KEY_c in _TYPING_CTRL_ABORT_KEYS
    assert KEY_C in _TYPING_CTRL_ABORT_KEYS
    assert KEY_d in _TYPING_CTRL_ABORT_KEYS
    assert KEY_D in _TYPING_CTRL_ABORT_KEYS


def test_modifier_keys_contain_expected():
    for k in (KEY_Shift_L, KEY_Shift_R, KEY_Control_L, KEY_Alt_L, KEY_Caps_Lock):
        assert k in _TYPING_PURE_MODIFIER_KEYS


# ── state transitions ───────────────────────────────────────────────────────

def test_initial_false_character_sets_typing():
    assert key(False, KEY_a) is True


def test_enter_clears_typing():
    assert key(True, KEY_Return) is False


def test_kp_enter_clears_typing():
    assert key(True, KEY_KP_Enter) is False


def test_escape_clears_typing():
    assert key(True, KEY_Escape) is False


def test_ctrl_c_clears_typing():
    assert key(True, KEY_c, ctrl=True) is False


def test_ctrl_C_clears_typing():
    assert key(True, KEY_C, ctrl=True) is False


def test_ctrl_d_clears_typing():
    assert key(True, KEY_d, ctrl=True) is False


def test_ctrl_c_without_ctrl_sets_typing():
    # bare 'c' without Ctrl modifier → composing
    assert key(False, KEY_c, ctrl=False) is True


def test_modifier_alone_does_not_change_state_when_false():
    assert key(False, KEY_Shift_L) is False


def test_modifier_alone_does_not_change_state_when_true():
    assert key(True, KEY_Shift_L) is True


def test_modifier_preserves_true():
    for km in (KEY_Shift_L, KEY_Shift_R, KEY_Control_L, KEY_Alt_L, KEY_Caps_Lock):
        assert key(True, km) is True, f"modifier {km:#x} should preserve True"


def test_backspace_sets_typing():
    # Backspace is editing — user is still composing
    assert key(False, KEY_BackSpace) is True


def test_space_sets_typing():
    assert key(False, KEY_Space) is True


def test_sequence_type_then_enter():
    state = False
    for k in [KEY_a, KEY_z, KEY_Space, KEY_a]:
        state = key(state, k)
    assert state is True
    state = key(state, KEY_Return)
    assert state is False


def test_sequence_type_pause_escape():
    state = False
    state = key(state, KEY_a)
    state = key(state, KEY_Escape)
    assert state is False


def test_ctrl_shift_c_does_not_set_typing():
    # Ctrl+Shift+C is the copy shortcut — user is not composing
    # shift is applied at event.state level; keyval is KEY_C (upper)
    # has_ctrl=True prevents setting typing
    assert key(False, KEY_C, ctrl=True) is False
