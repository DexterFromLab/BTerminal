"""BTerminal i18n — gettext wiring + locale resolution.

Public API:
    init_locale(language=None) -> str   # set up gettext, return resolved code
    current_language()         -> str   # active language code (e.g. 'en', 'pl')
    _(text)                    -> str   # translated text (or identity fallback)
    ngettext(s, p, n)          -> str   # plural-aware translation
    N_(text)                   -> str   # noop marker for module-level strings

Resolution chain (init_locale):
    1. explicit `language` argument
    2. options.json `language` key (None / "auto" -> next layer)
    3. LANGUAGE env var
    4. LANG env var
    5. fallback 'en'

Catalog lookup:
    locale/<lang>/LC_MESSAGES/bterminal.mo

If the .mo for the resolved language is missing, gettext.NullTranslations
is installed — `_()` and `ngettext()` return their msgid unchanged. This
matches the behaviour expected for `language='en'` (no catalog needed).

The module is import-safe: no GTK side-effects, lazy import of _OPTIONS
so the i18n module can be imported from bterminal.config without
circular trouble.
"""

from __future__ import annotations

import gettext
import os

DOMAIN = "bterminal"
LOCALE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "locale")

# Languages exposed in the Options dialog, in display order.
# Tuple format: (code, native_name, english_name).
# `english_name` is what we tell the AI agent when 'Tell AI my language' is on
# (the AI prompt itself is always English, so we say the language name in EN).
SUPPORTED_LANGUAGES = (
    ("en", "English",    "English"),
    ("pl", "Polski",     "Polish"),
    ("de", "Deutsch",    "German"),
    ("es", "Español",    "Spanish"),
    ("fr", "Français",   "French"),
    ("it", "Italiano",   "Italian"),
    ("pt", "Português",  "Portuguese"),
    ("ru", "Русский",    "Russian"),
    ("uk", "Українська", "Ukrainian"),
    ("cs", "Čeština",    "Czech"),
    ("zh", "中文",       "Chinese"),
    ("ja", "日本語",     "Japanese"),
    ("ko", "한국어",     "Korean"),
)


def language_english_name(code):
    """Return the English display name for a language code, or the code
    itself if unknown. Used in AI intro prompt hints."""
    for c, _native, en_name in SUPPORTED_LANGUAGES:
        if c == code:
            return en_name
    return code

_translation: gettext.NullTranslations = gettext.NullTranslations()
_active_lang: str = "en"


def _normalize(code: str | None) -> str | None:
    """Strip encoding and territory: 'pl_PL.UTF-8' -> 'pl'. Returns None
    for falsy / 'auto' / 'C' / 'POSIX' so the caller falls through to
    the next resolution layer."""
    if not code:
        return None
    code = code.strip()
    if code.lower() in ("auto", "c", "posix", ""):
        return None
    # Take everything before the first '.' (encoding) and '_' (territory).
    head = code.split(".", 1)[0].split("_", 1)[0]
    return head or None


def _resolve_language(explicit: str | None) -> str:
    """Run the resolution chain, returning the language code that should
    be active. Always returns a non-empty string; defaults to 'en'."""
    candidates = [
        explicit,
        _options_language(),
        os.environ.get("LANGUAGE"),
        os.environ.get("LANG"),
    ]
    for raw in candidates:
        norm = _normalize(raw)
        if norm:
            return norm
    return "en"


def _options_language() -> str | None:
    """Read options.json `language` lazily — avoids circular import
    when bterminal.config imports from this module."""
    try:
        from bterminal.config import _OPTIONS
    except Exception:
        return None
    return _OPTIONS.get("language")


def init_locale(language: str | None = None) -> str:
    """Resolve language and install the matching gettext catalog.

    Safe to call multiple times — replaces the active translation each
    call. If the catalog for the resolved language is missing,
    NullTranslations is installed (identity behaviour, no error).

    Returns the resolved language code (always non-empty).
    """
    global _translation, _active_lang
    _active_lang = _resolve_language(language)
    try:
        _translation = gettext.translation(
            DOMAIN, LOCALE_DIR, languages=[_active_lang], fallback=True,
        )
    except Exception:
        _translation = gettext.NullTranslations()
    return _active_lang


def current_language() -> str:
    """Return the currently active language code (e.g. 'en', 'pl')."""
    return _active_lang


def _(text: str) -> str:
    """Translate a single string. Returns msgid unchanged when no
    catalog is loaded for the active language (fallback)."""
    return _translation.gettext(text)


def ngettext(singular: str, plural: str, n: int) -> str:
    """Plural-aware translation. Falls back to English plural rule
    (n == 1 -> singular) when no catalog is loaded."""
    return _translation.ngettext(singular, plural, n)


def N_(text: str) -> str:
    """Noop marker for module-level / lazy strings. Caller wraps the
    actual lookup at use time::

        ERROR_MSG = N_("Cannot connect")
        ...
        show_error(_(ERROR_MSG))

    xgettext extracts these as translatable strings."""
    return text


# ─── Live translation refresh ────────────────────────────────────────────────
#
# GTK widgets store their labels at construction time, so flipping the
# active locale via `init_locale()` does NOT update widgets that already
# exist on screen — only fresh ones built afterwards. Without help the
# user has to restart the app to see the new language.
#
# The registry below tracks "translatable" widgets so we can re-apply
# their msgid through `_()` after a locale change. Callers register a
# (widget, msgid, refresh_callback) triple via `register_translatable()`,
# typically through the convenience helpers `tr()`, `tr_fmt()`,
# `tr_markup_bold()`. The OptionsDialog (or any locale switcher) calls
# `refresh_translatables()` after `init_locale()`.
#
# Widgets are held by weak reference so destroyed widgets don't leak;
# each refresh pass GCs entries whose widget has been collected.

# Registry holds (widget, msgid, refresh) triples. We previously used
# weakref but GObject-derived widgets (Gtk.MenuItem, Gtk.Label, ...) can
# be GC'd from Python eagerly even while still alive in the GTK tree
# (the C-side container holds them, but PyObject wrapper may not have a
# Python strong ref). That dropped menubar items, the sidebar header and
# anything held only by the Gtk parent. Strong refs + GTK "destroy" signal
# cleanup gives us reliable lifetime tracking instead.
_translatables: list = []


def register_translatable(widget, msgid: str, refresh) -> None:
    """Register `widget` so that `refresh(widget, _(msgid))` runs every
    time the active locale changes (via `refresh_translatables()`).

    `refresh` is a callable `(widget, translated_text) -> None`. The
    widget is held by strong reference; if it's a Gtk widget that
    supports the `destroy` signal, we connect to it so the entry is
    removed automatically once the widget is torn down.
    """
    entry = [widget, msgid, refresh]  # mutable so connect closure can null
    _translatables.append(entry)

    # Best-effort auto-cleanup on widget destroy. Plain Python objects
    # (test fakes) won't have `connect`; that's fine, manual removal or
    # full process exit cleans up.
    connect = getattr(widget, "connect", None)
    if callable(connect):
        try:
            def _drop_entry(_w, _entry=entry):
                _entry[0] = None  # mark dead; refresh prunes it
            connect("destroy", _drop_entry)
        except (TypeError, ValueError):
            pass


def refresh_translatables() -> int:
    """Re-translate every registered widget against the currently active
    catalog. Returns the number of widgets actually refreshed (dropped
    entries are pruned from the registry). Idempotent."""
    global _translatables
    alive: list = []
    refreshed = 0
    import os
    debug = os.environ.get("BTERMINAL_I18N_DEBUG")
    for entry in _translatables:
        widget, msgid, refresh = entry
        if widget is None:
            continue  # dropped via destroy signal
        try:
            refresh(widget, _(msgid))
            # Coax GTK into a redraw — set_label on already-shown widgets
            # (especially Gtk.MenuItem with its built-in AccelLabel child)
            # sometimes retains the cached visual until the next event.
            if hasattr(widget, "queue_resize"):
                try:
                    widget.queue_resize()
                except Exception:
                    pass
            if hasattr(widget, "queue_draw"):
                try:
                    widget.queue_draw()
                except Exception:
                    pass
            refreshed += 1
            alive.append(entry)
            if debug:
                print(
                    f"[i18n.refresh] {type(widget).__name__} "
                    f"msgid={msgid!r} -> {_(msgid)!r}",
                    flush=True,
                )
        except Exception as exc:
            # Widget may be in a destroyed / invalid state; drop it.
            if debug:
                print(f"[i18n.refresh] DROP msgid={msgid!r} ({exc})", flush=True)
    _translatables = alive
    if debug:
        print(f"[i18n.refresh] refreshed={refreshed}, alive={len(alive)}", flush=True)
    return refreshed


def tr(widget, method_name: str, msgid: str) -> None:
    """Apply a translated string to `widget.<method_name>` AND register
    the widget for live refresh on subsequent locale changes.

    Usage examples::

        tr(button,  "set_label",        "Sessions")
        tr(button,  "set_tooltip_text", "Show sidebar (Ctrl+B)")
        tr(window,  "set_title",        "Settings")
    """
    getattr(widget, method_name)(_(msgid))
    register_translatable(
        widget, msgid,
        lambda w, t, _m=method_name: getattr(w, _m)(t),
    )


def tr_fmt(widget, method_name: str, msgid_template: str, **placeholders) -> None:
    """Translate `msgid_template`, `.format(**placeholders)` it, and
    apply via `widget.<method_name>`. Re-formats with the same
    placeholders on every locale refresh.

    Use when the visible string interpolates runtime values that don't
    themselves change with locale (e.g. an app name)::

        tr_fmt(label, "set_label", "{app} Sessions", app=APP_NAME)
    """
    def _build():
        return _(msgid_template).format(**placeholders)
    getattr(widget, method_name)(_build())
    register_translatable(
        widget, msgid_template,
        lambda w, _t, _m=method_name, _b=_build: getattr(w, _m)(_b()),
    )


def tr_markup_bold(widget, msgid: str) -> None:
    """Set bold Pango markup with a translated body. Convenience for
    the common ``"<b>" + _(msgid) + "</b>"`` pattern."""
    def _wrap():
        return "<b>" + _(msgid) + "</b>"
    widget.set_markup(_wrap())
    register_translatable(
        widget, msgid,
        lambda w, _t, _b=_wrap: w.set_markup(_b()),
    )
