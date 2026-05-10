"""i18n end-to-end: Polish CLAUDE.md → AIDER.md → aider feed UTF-8
(#45 / #117, audit § 6.4 #18).

User's project has CLAUDE.md authored in Polish (ąęćśźżłóń + special
chars: em-dash, smart quotes, Unicode bullets). The full pipeline
must preserve UTF-8 byte-for-byte:

  1. ctx wizard creates AIDER.md → CLAUDE.md symlink — Polish
     content survives (filesystem stores bytes, not chars).
  2. Symlink resolution returns identical bytes when reading
     AIDER.md vs CLAUDE.md.
  3. extract_rules_inject_bytes round-trips Polish text from
     `ctx rules inject` stdout.
  4. _format_image_paste_for_provider handles Polish paths
     (screenshots with polskie znaki).
  5. AiderProvider.parse_session_stats reads `.aider.chat.history.md`
     containing Polish content without mojibake.

Three decision branches:
  (a) Polish in headings — `# Cele projektu` / `## Założenia` /
      `### Gotowe`. Standard Markdown headings — no encoding hazard.
  (b) Polish in code blocks — fenced ``` blocks may include
      Polish comments. UTF-8 round-trip + format() substitution
      must survive.
  (c) Polish in inline rules block — bullet text with mixed
     ASCII/UTF-8 — most common real-world case.

Pinned defenses:
  - All file I/O uses `encoding="utf-8"` explicitly.
  - bytes encoding is `.encode("utf-8")` (not default `.encode()`).
  - Aider provider's parse_session_stats opens with
    `encoding="utf-8", errors="replace"` (resilient to corrupted
    bytes mid-write — see #28).

Manual VM smoke (project z Polish CLAUDE.md, spawn aider, ask
"co jest w CLAUDE.md") is documented in tests/manual/README.md.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bterminal.ctx.helpers import (
    ensure_context_file_alongside_claude,
    ensure_context_files_for_all_providers,
)
from bterminal.helpers import format_image_paste_hint
from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.providers.aider import AiderProvider
from bterminal.ui.terminal_tab import extract_rules_inject_bytes


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


# ─── Sample Polish content (representative real-world cases) ────────────


POLISH_HEADINGS = """\
# Projekt: Zaawansowany analizator słów kluczowych

## Założenia

Główne cele projektu to:
- Wydobywanie kluczowych haseł z tekstu źródłowego.
- Łączenie się z bazą danych poprzez sterownik Postgres.
- Rozróżnianie znaków ąęćśźżłóń podczas tokenizacji.

## Gotowe komponenty

### Backend
Działa już moduł `tokenizer` rozpoznający polskie końcówki.

### Frontend
Próbujemy złożyć interfejs użytkownika (UI) — bardzo wcześnie.
"""


POLISH_CODE_BLOCK = """\
# Konfiguracja

```python
# Przykład: funkcja obsługująca polskie znaki diakrytyczne
def normalizuj(tekst: str) -> str:
    \"\"\"Konwertuje 'ąęćśźżłóń' do ASCII.\"\"\"
    mapowanie = {
        "ą": "a", "ę": "e", "ć": "c", "ś": "s",
        "ź": "z", "ż": "z", "ł": "l", "ó": "o", "ń": "n",
    }
    return "".join(mapowanie.get(c, c) for c in tekst)
```

Wyjaśnienie: ten kod normalizuje ciąg znaków do form bezogonkowych.
"""


POLISH_RULES_BLOCK = """\
## Reguły projektu

- Bądź zwięzły — odpowiadaj krótko.
- Nigdy nie usuwaj plików bez zapytania.
- Pisz testy w stylu TDD: najpierw test, potem implementacja.
- Komunikuj się w języku polskim, używając pełnych form (ą, ę, ć, ś, ź, ż).
- Dla każdej zmiany dokumentuj „Co?" oraz „Dlaczego?" — bez „jak".
"""


# ─── Branch (a): Polish in headings — CLAUDE.md → AIDER.md symlink ──────


def test_polish_headings_in_claude_md_preserved_through_symlink(tmp_path):
    """CLAUDE.md with Polish headings → AIDER.md symlink resolves
    to identical bytes. ctx wizard's mirror flow must not trans-
    code or strip diacritics."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(POLISH_HEADINGS, encoding="utf-8")

    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "symlink"

    aider_md = tmp_path / "AIDER.md"
    # Bytes-level identical
    assert aider_md.read_bytes() == claude_md.read_bytes()
    # Polish chars survive UTF-8 round-trip
    text = aider_md.read_text(encoding="utf-8")
    # Sample DOES contain the full alphabet block on one line
    # ('Rozróżnianie znaków ąęćśźżłóń') — pin survival
    assert "ąęćśźżłóń" in text
    # Plus individual diacritic chars in scattered positions
    for ch in "ą ę ś ź ż ł ó ń".split():
        assert ch in text, (
            f"diacritic {ch!r} lost in symlink resolution"
        )
    # Em-dash + Polish word combos
    assert "Założenia" in text
    assert "Próbujemy" in text


def test_polish_headings_via_dispatcher(tmp_path):
    """Same scenario via ensure_context_files_for_all_providers
    (production callsite). Both AIDER.md and AGENTS.md should
    resolve to identical Polish content."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(POLISH_HEADINGS, encoding="utf-8")

    results = ensure_context_files_for_all_providers(tmp_path)
    assert results.get("AIDER.md") == "symlink"
    assert results.get("AGENTS.md") == "symlink"

    aider_text = (tmp_path / "AIDER.md").read_text(encoding="utf-8")
    agents_text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert aider_text == agents_text == POLISH_HEADINGS


# ─── Branch (b): Polish in code blocks — survives format() ──────────────


def test_polish_code_block_survives_symlink_round_trip(tmp_path):
    """Code blocks may contain Polish comments + string literals
    with diacritics. Pin: nothing strips them through the
    symlink path."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(POLISH_CODE_BLOCK, encoding="utf-8")

    ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    aider_md = tmp_path / "AIDER.md"
    text = aider_md.read_text(encoding="utf-8")

    # Polish docstring + string mapping survives
    assert "Konwertuje 'ąęćśźżłóń' do ASCII" in text
    # Code-block fences intact
    assert text.count("```") == 2
    # Polish word in surrounding prose
    assert "Wyjaśnienie" in text


def test_polish_code_block_format_substitution_survives():
    """If a future helper does .format() substitution on Polish
    content (e.g. injecting project name into a Polish-content
    template), pin that string.format() round-trips Polish chars."""
    template = (
        "Projekt: {name} — moduł {module} obsługuje znaki "
        "ąęćśźż dla użytkownika końcowego."
    )
    out = template.format(name="Słowniki", module="tokenizer")
    assert "Słowniki" in out
    assert "ąęćśźż" in out
    # Bytes round-trip via UTF-8
    encoded = out.encode("utf-8")
    decoded = encoded.decode("utf-8")
    assert decoded == out


# ─── Branch (c): Polish inline rules block via extract_rules_inject_bytes


def test_polish_rules_block_round_trips_via_extract_rules_inject_bytes():
    """The canonical rules-injection path: `ctx rules inject`
    stdout (Polish) → extract_rules_inject_bytes → UTF-8 bytes
    fed to feed_child. Pin: every Polish character survives the
    encode/decode cycle."""
    out_bytes = extract_rules_inject_bytes(
        "aider", "myproj", POLISH_RULES_BLOCK)
    # bytes type
    assert isinstance(out_bytes, bytes)
    # Round-trip UTF-8 decode
    text = out_bytes.decode("utf-8")
    # Every diacritic from input present in output
    for ch in "ąęćśźżłóńĄĘĆŚŹŻŁÓŃ":
        if ch in POLISH_RULES_BLOCK:
            assert ch in text, (
                f"diacritic {ch!r} lost in extract_rules_inject_bytes"
            )
    # Em-dash survives
    assert "—" in text
    # Smart quotes survive
    assert "„" in text or '„' in text


def test_polish_rules_block_byte_identical_across_three_providers():
    """The bytes are identical for Claude/Copilot/Aider (provider-
    agnostic helper, pinned by #93). Polish content is no
    exception."""
    bytes_per_provider = {
        name: extract_rules_inject_bytes(name, "myproj", POLISH_RULES_BLOCK)
        for name in ("claude", "copilot", "aider")
    }
    first = bytes_per_provider["claude"]
    for name, bs in bytes_per_provider.items():
        assert bs == first, (
            f"{name} Polish rules bytes diverged from Claude — "
            f"first={first!r}, {name}={bs!r}"
        )


def test_polish_rules_block_strips_trailing_whitespace_only():
    """Pre-#93 contract: trailing whitespace stripped, INNER
    whitespace + Polish content preserved. Pin with Polish-heavy
    sample so a Unicode-aware strip refactor can't accidentally
    drop diacritic chars."""
    rules_with_trailing = POLISH_RULES_BLOCK + "   \n\n   "
    out = extract_rules_inject_bytes(
        "aider", "myproj", rules_with_trailing).decode("utf-8")
    # No trailing whitespace
    assert not out.endswith(" ")
    assert not out.endswith("\n")
    # Inner content intact (last bullet still there)
    assert "Dlaczego" in out


# ─── Branch (d additional): Polish chat history → parse_session_stats ───


def test_polish_chat_history_parsed_without_mojibake(tmp_path):
    """Aider writes `.aider.chat.history.md` with Polish content
    (user prompts and replies in Polish). AiderProvider.
    parse_session_stats reads with `encoding='utf-8',
    errors='replace'` — Polish content round-trips."""
    history = tmp_path / ".aider.chat.history.md"
    history.write_text(
        "# aider chat\n\n"
        "#### Co jest w CLAUDE.md?\n\n"
        "Plik CLAUDE.md zawiera reguły projektu — opisane "
        "wcześniej z polskimi znakami: ąęćśźżłóń.\n\n"
        "> Tokens: 1.5k sent, 234 received.\n\n"
        "#### Czy znajdziesz wzmiankę o tokenizerze?\n\n"
        "Tak, moduł tokenizer rozpoznaje polskie końcówki "
        "(np. -em, -ą, -ość).\n\n"
        "> Tokens: 800 sent, 100 received.\n",
        encoding="utf-8",
    )

    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    stats = aider.parse_session_stats(str(history))

    # Two #### user-turn markers
    assert stats.response_count == 2
    # Token counts captured (1.5k+800 = 2300 input; 234+100 = 334 output)
    assert stats.input_tokens == 1500 + 800
    assert stats.output_tokens == 234 + 100


def test_polish_chat_history_with_mixed_utf8_and_ascii_safe(tmp_path):
    """Mixed lines — some pure ASCII, some Polish. Parser handles
    both transparently (no encoding flag toggling per line)."""
    history = tmp_path / ".aider.chat.history.md"
    history.write_text(
        "# chat\n\n"
        "#### Run pytest tests/test_polish.py\n\n"
        "Done — wynik: 5 passed, 0 failed.\n\n"
        "> Tokens: 100 sent, 50 received.\n\n"
        "#### Wyświetl błąd jeśli wystąpi.\n\n"
        "Brak błędów. Pomyślnie zakończone.\n\n"
        "> Tokens: 80 sent, 30 received.\n",
        encoding="utf-8",
    )

    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    stats = aider.parse_session_stats(str(history))
    assert stats.response_count == 2
    assert stats.input_tokens == 180
    assert stats.output_tokens == 80


def test_polish_chat_history_with_partial_utf8_byte_safe(tmp_path):
    """If aider's chat history was truncated mid-multibyte UTF-8
    (say, mid-`ą`), the parser must use `errors='replace'` so it
    doesn't crash. Pin: AiderProvider's parse_session_stats has
    that decoding flag."""
    raw = (
        "# chat\n\n"
        "#### Pytanie\n\n"
        "Odpowiedź — koniec.\n\n"
        "> Tokens: 100 sent, 50 received.\n"
    ).encode("utf-8") + b"\xc4"  # half of `ą` (\xc4\x85)

    history = tmp_path / ".aider.chat.history.md"
    history.write_bytes(raw)

    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    stats = aider.parse_session_stats(str(history))
    # Tokens captured despite trailing partial UTF-8
    assert stats.input_tokens == 100
    assert stats.output_tokens == 50
    assert stats.response_count == 1


# ─── Image paste with Polish path (screenshots with diacritics) ─────────


def test_image_paste_template_substitutes_polish_path():
    """User saves a screenshot with Polish filename. The paste
    template wraps {path} verbatim — diacritics survive."""
    polish_path = "/home/użytkownik/Pulpit/zrzut-ekranu-projekt-ąęć.png"
    reg = ProviderRegistry(config=load_providers_config())
    template = reg.get("aider")._argv_spec["image_paste_template"]

    out = format_image_paste_hint(template, polish_path)
    # Path embedded verbatim
    assert polish_path in out
    # Polish components survive
    for ch in ("użytkownik", "Pulpit", "zrzut-ekranu", "ąęć"):
        assert ch in out


def test_image_paste_template_with_polish_template_text():
    """Future scenario: per-session override with Polish hint
    text. Pin that .format() handles Polish substitution AND the
    {path} placeholder simultaneously."""
    polish_template = (
        "Wkleiłem obraz: {path} — proszę najpierw opisz, co widzisz."
    )
    out = format_image_paste_hint(
        polish_template, "/tmp/ekran.png")
    assert out == (
        "Wkleiłem obraz: /tmp/ekran.png — proszę najpierw opisz, "
        "co widzisz."
    )


# ─── Cross: ensure_context_file ZACHOWUJE bytes-perfect symlink ─────────


def test_polish_claude_md_byte_identical_via_symlink_chain(tmp_path):
    """Full chain pin: write CLAUDE.md → symlink AIDER.md →
    symlink AGENTS.md (via dispatcher) → all 3 read identical
    bytes. Critical for Claude+Copilot+Aider multi-provider
    sessions on Polish projects."""
    full_polish = (
        POLISH_HEADINGS + "\n\n" +
        POLISH_CODE_BLOCK + "\n\n" +
        POLISH_RULES_BLOCK
    )
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(full_polish, encoding="utf-8")
    original_bytes = claude_md.read_bytes()

    ensure_context_files_for_all_providers(tmp_path)

    # All three files have BYTE-identical content
    for fn in ("CLAUDE.md", "AIDER.md", "AGENTS.md"):
        assert (tmp_path / fn).read_bytes() == original_bytes, (
            f"{fn} bytes diverged from CLAUDE.md — encoding "
            f"transformation in symlink chain"
        )


# ─── format() substitution and bytes encoding pinned consistent ─────────


def test_extract_rules_inject_bytes_uses_utf8_encoding_explicitly():
    """Pin: extract_rules_inject_bytes encodes via .encode()
    which defaults to UTF-8 in Python 3. Pin so a refactor that
    changes to ASCII / latin-1 fails immediately on Polish input."""
    polish = "Reguła: bądź ostrożny — używaj ąęć."
    out = extract_rules_inject_bytes("aider", "myproj", polish)
    # Bytes match UTF-8 encoding (no surrogateescape, no other codec)
    assert out == polish.strip().encode("utf-8")


def test_format_image_paste_hint_returns_str_not_bytes():
    """Pin: format_image_paste_hint returns str (not bytes). The
    caller (terminal_tab._paste_clipboard_image_path) is
    responsible for encoding to bytes when needed. Pin so a
    refactor that pre-encodes doesn't introduce double-encoding
    of Polish chars."""
    polish_template = "Obraz: {path} — opisz."
    out = format_image_paste_hint(polish_template, "/tmp/ą.png")
    assert isinstance(out, str)
    # Round-trip UTF-8
    assert out.encode("utf-8").decode("utf-8") == out


# ─── Defensive: parser robust to Polish + emoji combos ──────────────────


def test_aider_chat_history_with_polish_and_emoji(tmp_path):
    """Combined stress: Polish + emoji + ASCII. Real-world chats
    often have both."""
    history = tmp_path / ".aider.chat.history.md"
    history.write_text(
        "# chat 🦫\n\n"
        "#### Sprawdź czy to działa ✅\n\n"
        "Tak, działa! Wszystko OK 🎉. Polskie znaki: ąęćśźżłóń ✓.\n\n"
        "> Tokens: 100 sent, 50 received.\n",
        encoding="utf-8",
    )

    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    stats = aider.parse_session_stats(str(history))
    assert stats.response_count == 1
    assert stats.input_tokens == 100
    assert stats.output_tokens == 50


def test_extract_rules_inject_bytes_with_unicode_normalization_forms():
    """Polish chars come in different Unicode normalization
    forms (NFC: precomposed `ą`; NFD: `a` + combining ogonek
    U+0328). Both should survive UTF-8 round-trip."""
    nfc = "ą"  # precomposed (1 codepoint)
    nfd = "ą"  # decomposed (2 codepoints, same visual)

    out_nfc = extract_rules_inject_bytes(
        "aider", "p", f"Reguła z {nfc} (NFC)")
    out_nfd = extract_rules_inject_bytes(
        "aider", "p", f"Reguła z {nfd} (NFD)")

    # Both produce valid UTF-8 bytes
    assert out_nfc.decode("utf-8") == f"Reguła z {nfc} (NFC)"
    assert out_nfd.decode("utf-8") == f"Reguła z {nfd} (NFD)"
    # And the byte payloads differ (different Unicode forms)
    assert out_nfc != out_nfd


# ─── Source-grep: explicit utf-8 encoding throughout the chain ──────────


def test_aider_provider_opens_chat_history_with_utf8_encoding():
    """Pin: AiderProvider.parse_session_stats opens the chat
    history with `encoding='utf-8', errors='replace'`. Without
    this, OS-default locale (latin-1 on minimal containers) would
    crash on Polish content."""
    src = (Path(__file__).resolve().parent.parent
           / "bterminal" / "providers" / "aider.py").read_text()
    fn_start = src.find("def parse_session_stats")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert 'encoding="utf-8"' in body, (
        "parse_session_stats no longer uses explicit UTF-8 — "
        "Polish content may crash on latin-1 default locales"
    )
    assert 'errors="replace"' in body or "errors='replace'" in body


def test_ensure_context_file_uses_symlink_not_re_encode(tmp_path):
    """The symlink path doesn't read+write the source file —
    just creates a kernel-level link. So bytes are NEVER
    transcoded. Pin via test that bytes match exactly (no
    BOM addition, no newline conversion)."""
    raw_polish = "ąęćśźżłóń\r\nlinia 2".encode("utf-8")
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_bytes(raw_polish)

    ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    aider_bytes = (tmp_path / "AIDER.md").read_bytes()

    # Byte-identical (including \r\n line endings, if any)
    assert aider_bytes == raw_polish
