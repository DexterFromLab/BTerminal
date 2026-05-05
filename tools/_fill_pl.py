#!/usr/bin/env python3
"""Fill PL translations into locale/pl/LC_MESSAGES/bterminal.po.

One-shot script for F3.c. Reads the auto-generated .po, sets msgstr for
each msgid based on the mapping below (recovered from the pre-F2 PL
strings that lived in bterminal/<file>.py before the marker pass), and
writes the result back. Plural forms are handled via msgstr[0/1/2]
mapping with Polish nplurals=3 rules.

After running this script: ./tools/i18n.sh compile (msgfmt to .mo).
"""

from babel.messages.pofile import read_po, write_po

PO_PATH = "locale/pl/LC_MESSAGES/bterminal.po"

# Singular translations.
TRANSLATIONS = {
    # license.py
    "BTerminal — License Agreement ({context})": "BTerminal — Umowa licencyjna ({context})",
    "Decline and exit": "Odrzuć i wyjdź",
    "Accept": "Akceptuję",
    "Please read the license agreement below.": "Proszę przeczytać poniższą umowę licencyjną.",
    "You must accept these terms to use BTerminal.": "Musisz zaakceptować te warunki, aby korzystać z BTerminala.",
    "I have read and accept the license terms.": "Przeczytałem i akceptuję warunki licencji.",
    "First run": "Pierwsze uruchomienie",
    "Update": "Aktualizacja",

    # updater.py
    "No repository": "Brak repozytorium",
    "Cannot check for updates — repository directory not found.":
        "Nie można sprawdzić aktualizacji — katalog repozytorium nie został znaleziony.",
    "Checking for updates": "Sprawdzanie aktualizacji",
    "Connecting to server... ({seconds}s)": "Łączenie z serwerem... ({seconds}s)",
    "Cancel": "Anuluj",
    "Cannot check for updates — timed out.": "Nie można sprawdzić — przekroczono limit czasu.",
    "Close": "Zamknij",
    "BTerminal is up to date. No new updates.": "BTerminal jest aktualny. Brak nowych aktualizacji.",
    "Cannot check for updates.": "Nie można sprawdzić aktualizacji.",
    "New BTerminal version": "Nowa wersja BTerminal",
    "A new version of BTerminal is available": "Dostępna nowa wersja BTerminal",
    "Show errata": "Pokaż erratę",
    "Not now": "Nie teraz",
    "Update and restart": "Aktualizuj i uruchom ponownie",
    "BTerminal errata": "Errata BTerminal",
    "No errata entries.": "Brak wpisów errata.",
    "BTerminal update": "Aktualizacja BTerminal",
    "Update in progress…": "Aktualizacja w toku…",
    (
        "The new version of BTerminal could not be installed.\n\n"
        "The previous version was restored automatically — "
        "BTerminal continues to work normally."
    ): (
        "Nowa wersja BTerminal nie mogła zostać zainstalowana.\n\n"
        "Poprzednia wersja została automatycznie przywrócona — "
        "BTerminal działa normalnie."
    ),
    (
        "Installation failed and no previous version is available "
        "to restore.\n\nDetails:\n{details}"
    ): (
        "Instalacja nie powiodła się i nie ma poprzedniej wersji do przywrócenia.\n\n"
        "Szczegóły:\n{details}"
    ),

    # app.py
    "New local tab": "Nowa karta lokalna",
    "New SSH session…": "Nowa sesja SSH…",
    "New Claude Code session…": "Nowa sesja Claude Code…",
    "Options…": "Opcje…",
    "Quit": "Zamknij aplikację",
    "File": "Plik",
    "Toggle sidebar (Ctrl+B)": "Przełącz sidebar (Ctrl+B)",
    "Toggle Git panel (Ctrl+G)": "Przełącz panel Git (Ctrl+G)",
    "Toggle theme ☀/🌙": "Przełącz motyw ☀/🌙",
    "Sessions": "Sesje",
    "Ctx": "Ctx",
    "Consult": "Konsultacja",
    "Tasks": "Zadania",
    "Plugins": "Wtyczki",
    "View": "Widok",
    "Check for updates": "Sprawdź aktualizacje",
    "Errata…": "Errata…",
    "Tools": "Narzędzia",
    (
        "The file ~/.config/bterminal/options.json was corrupted — "
        "default settings have been restored.\n\n"
        "Cause: {exc_type}: {exc}"
    ): (
        "Plik ~/.config/bterminal/options.json był uszkodzony — "
        "przywrócono ustawienia domyślne.\n\n"
        "Przyczyna: {exc_type}: {exc}"
    ),

    # ctx/dialogs.py
    (
        "Project context has not been collected yet. "
        "Gather context as you work and save important findings: "
        "ctx set <project> <key> <value>"
    ): (
        "Kontekst projektu nie został jeszcze zebrany. "
        "Zbierz kontekst w trakcie pracy i zapisuj ważne odkrycia: "
        "ctx set <project> <key> <value>"
    ),

    # ui/dialogs/options.py
    "Appearance": "Wygląd",
    "Theme:": "Motyw:",
    "Dark (Mocha)": "Ciemny (Mocha)",
    "Light (Latte)": "Jasny (Latte)",
    "Terminal font:": "Font terminala:",
    "Terminal": "Terminal",
    "Default shell:": "Domyślny shell:",
    "default ({shell})": "domyślny ({shell})",
    "General": "Ogólne",
    "Check for updates at startup:": "Sprawdzaj aktualizacje przy starcie:",
    "Save": "Zapisz",

    # ui/panels/git.py
    "Working tree clean": "Drzewo robocze czyste",

    # app.py — sidebar tooltips (added after first F2 pass)
    "Hide sidebar (Ctrl+B)": "Ukryj sidebar (Ctrl+B)",
    "Show sidebar (Ctrl+B)": "Pokaż sidebar (Ctrl+B)",
    "Toggle light/dark theme": "Przełącz motyw jasny/ciemny",
    "Show Git panel (Ctrl+G)": "Pokaż panel Git (Ctrl+G)",
    "Memory": "Memory",  # technical name, kept identical
    "Files": "Pliki",
    "Skills": "Skills",  # technical name in this app, kept identical
    # ui/sidebar.py header
    "{app} Sessions": "Sesje {app}",

    # ui/dialogs/options.py — Language section (F5)
    "Language": "Język",
    "Interface language:": "Język interfejsu:",
    "Auto-detect": "Wykryj automatycznie",
    "Restart BTerminal to apply language change.":
        "Uruchom BTerminala ponownie, aby zastosować zmianę języka.",
    "Tell the AI agent which language I speak":
        "Powiedz agentowi AI, w jakim języku mówię",
}

# Plural-form translations: msgid -> tuple of 3 forms (Polish nplurals=3).
# Plural index map: 0=singular (n==1), 1=few (2-4 except 12-14), 2=many (rest).
PLURALS = {
    ("{n} file", "{n} files"): (
        "{n} plik",     # n==1
        "{n} pliki",    # 2-4 (except teens)
        "{n} plików",   # 0, 5-21, 22-24, ...
    ),
}


def main() -> int:
    with open(PO_PATH, "rb") as fh:
        catalog = read_po(fh)

    misses_singular: list[str] = []
    misses_plural: list[tuple[str, str]] = []

    for message in catalog:
        if not message.id or message.id == "":
            continue  # skip header
        if isinstance(message.id, tuple):
            # Plural message: (singular, plural) ids
            tr = PLURALS.get(message.id)
            if tr is None:
                misses_plural.append(message.id)
                continue
            message.string = tr
        else:
            tr = TRANSLATIONS.get(message.id)
            if tr is None:
                misses_singular.append(message.id)
                continue
            message.string = tr

    with open(PO_PATH, "wb") as fh:
        write_po(fh, catalog, width=0)

    total = len(catalog) - 1  # minus header
    filled = total - len(misses_singular) - len(misses_plural)
    print(f"Filled {filled}/{total} msgids")
    if misses_singular:
        print(f"Untranslated singular ({len(misses_singular)}):")
        for m in misses_singular:
            print(f"  - {m!r}")
    if misses_plural:
        print(f"Untranslated plural ({len(misses_plural)}):")
        for m in misses_plural:
            print(f"  - {m!r}")
    return 0 if not (misses_singular or misses_plural) else 1


if __name__ == "__main__":
    raise SystemExit(main())
