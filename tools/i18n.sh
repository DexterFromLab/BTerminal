#!/bin/bash
# i18n.sh — gettext catalog management for BTerminal.
#
# Targets:
#   extract              — xgettext over bterminal/ -> locale/bterminal.pot
#   update               — msgmerge bterminal.pot into each locale/<lang>/LC_MESSAGES/bterminal.po
#   compile              — msgfmt each .po -> .mo (run by install.sh too)
#   new <lang>           — msginit -l <lang> -i bterminal.pot -> locale/<short>/LC_MESSAGES/bterminal.po
#                          <lang> uses POSIX form, e.g. pl_PL, de_DE, fr_FR
#   stats                — print untranslated/fuzzy counts per .po
#
# Typical workflow when adding new translatable strings:
#   ./tools/i18n.sh extract    # refresh .pot
#   ./tools/i18n.sh update     # propagate new msgids into existing .po files
#   # ...edit .po files (Poedit recommended)...
#   ./tools/i18n.sh compile    # produce .mo files runtime needs
#
# Adding a new language:
#   ./tools/i18n.sh new de_DE
#   # ...translate locale/de/LC_MESSAGES/bterminal.po...
#   ./tools/i18n.sh compile

set -euo pipefail
cd "$(dirname "$0")/.."

DOMAIN="bterminal"
POT="locale/${DOMAIN}.pot"

case "${1:-}" in
    extract)
        mkdir -p locale
        # --keyword=_:1   single-arg gettext (translatable msgid)
        # --keyword=N_:1  noop marker for module-level lazy strings
        # --keyword=ngettext:1,2  plural form
        find bterminal -name '*.py' -print0 \
            | xargs -0 xgettext \
                --language=Python \
                --from-code=UTF-8 \
                --keyword=_ \
                --keyword=N_ \
                --keyword=ngettext:1,2 \
                --keyword=tr:3 \
                --keyword=tr_fmt:3 \
                --keyword=tr_markup_bold:2 \
                --force-po \
                --package-name="$DOMAIN" \
                --copyright-holder="Bartosz Czarnota" \
                --msgid-bugs-address="bartoszczarnota1@gmail.com" \
                --output="$POT"
        echo "Extracted -> $POT"
        ;;
    update)
        if [[ ! -f "$POT" ]]; then
            echo "✗ $POT does not exist — run './tools/i18n.sh extract' first." >&2
            exit 1
        fi
        found=0
        for po in locale/*/LC_MESSAGES/${DOMAIN}.po; do
            [[ -f "$po" ]] || continue
            msgmerge --update --backup=none --quiet "$po" "$POT"
            echo "Updated $po"
            found=$((found+1))
        done
        if [[ "$found" -eq 0 ]]; then
            echo "(no .po files yet — use './tools/i18n.sh new <lang>' to create one)"
        fi
        ;;
    compile)
        found=0
        for po in locale/*/LC_MESSAGES/${DOMAIN}.po; do
            [[ -f "$po" ]] || continue
            mo="${po%.po}.mo"
            msgfmt --check --output-file="$mo" "$po"
            echo "Compiled $mo"
            found=$((found+1))
        done
        if [[ "$found" -eq 0 ]]; then
            echo "(no .po files to compile)"
        fi
        ;;
    new)
        lang="${2:-}"
        if [[ -z "$lang" ]]; then
            echo "Usage: $0 new <lang_code>   e.g. pl_PL, de_DE, fr_FR" >&2
            exit 1
        fi
        if [[ ! -f "$POT" ]]; then
            echo "✗ $POT does not exist — run './tools/i18n.sh extract' first." >&2
            exit 1
        fi
        # Short code = part before '_' (e.g. pl_PL -> pl) for the directory.
        short="${lang%%_*}"
        dir="locale/${short}/LC_MESSAGES"
        po="${dir}/${DOMAIN}.po"
        mkdir -p "$dir"
        if [[ -f "$po" ]]; then
            echo "✗ $po already exists — refusing to overwrite." >&2
            exit 1
        fi
        msginit --no-translator --locale="$lang" --input="$POT" --output-file="$po"
        echo "Initialized $po"
        echo "Edit it (Poedit recommended), then run './tools/i18n.sh compile'."
        ;;
    stats)
        for po in locale/*/LC_MESSAGES/${DOMAIN}.po; do
            [[ -f "$po" ]] || continue
            printf "%-40s  " "$po"
            msgfmt --statistics --output-file=/dev/null "$po" 2>&1 | head -1
        done
        ;;
    -h|--help|"")
        head -27 "$0" | tail -26
        ;;
    *)
        echo "✗ unknown target: $1" >&2
        echo "  use: extract | update | compile | new <lang> | stats | --help" >&2
        exit 1
        ;;
esac
