#!/usr/bin/env bash
# tools/_e2e_ui_driver.sh — semi-automatyczny UI driver dla VM E2E
# bug-fix workflow (rules #17-#23 w ctx bterminal).
#
# Przyjęte założenia:
#   - VM_HOST=vm-test (ssh alias)
#   - DISPLAY=:0 na VM
#   - BT zainstalowany w /home/michal/BTerminal
#   - smoke-logs/<bug>/ to docelowy katalog na PNG-i + actions.log
#
# Funkcje:
#   cleanup_bt              — pkill -9 wszystkich bterminal + verify 0 procesów
#   launch_bt LANG          — set language, cleanup, setsid -f, wait for window
#   sync_to_vm <files...>   — scp plików do VM repo
#   shot <bug> <step> <action>  — screenshot na VM, scp, zapis actions.log
#   click_at <bug> <step> <action> <x> <y>  — wmctrl activate + xdotool click + shot
#   key <bug> <step> <action> <key>         — xdotool key + shot
#   menubar_y               — zwraca screen-y środka menubar BT
#   find_menu_x <label>     — zwraca screen-x label-a w menubar (przez OCR/known)
#   pin_test <test_file>    — pytest na VM, return 0 jeśli all green
#
# Use-case (BUG#1):
#   source tools/_e2e_ui_driver.sh
#   launch_bt pl
#   click_menu bug1-fix 01 "open_tools" "Narzędzia"
#   # ... (visual review przez Read tool)
#   # ... fix kodu, sync_to_vm, kill, relaunch
#   click_menu bug1-fix 02 "open_tools_after_fix" "Narzędzia"
#   pin_test tests/e2e/test_tools_menu_pl_translation.py

VM_HOST="${VM_HOST:-vm-test}"
DISPLAY_REMOTE="${DISPLAY_REMOTE::0}"
DISPLAY_REMOTE="${DISPLAY_REMOTE:-:0}"
SMOKE_ROOT="${SMOKE_ROOT:-$(pwd)/smoke-logs}"
VM_BTERMINAL="${VM_BTERMINAL:-/home/michal/BTerminal}"

cleanup_bt() {
    timeout 8 ssh "$VM_HOST" 'pkill -9 -f "python3 -m bterminal" 2>/dev/null; sleep 1; pgrep -af "python3 -m bterminal" | wc -l' 2>&1 | tail -1
}

launch_bt() {
    local lang="${1:-en}"
    cleanup_bt >/dev/null
    timeout 10 ssh "$VM_HOST" "python3 -c '
import json, os
p = os.path.expanduser(\"~/.config/bterminal/options.json\")
d = json.load(open(p))
d[\"language\"] = \"$lang\"
open(p, \"w\").write(json.dumps(d, indent=2))
print(\"LANG=$lang OK\")
'" >/dev/null
    timeout 8 ssh "$VM_HOST" "cd $VM_BTERMINAL && DISPLAY=$DISPLAY_REMOTE setsid -f python3 -m bterminal > /tmp/_bt_e2e.log 2>&1 < /dev/null" >/dev/null
    # Wait for window
    local i=0
    while [[ $i -lt 12 ]]; do
        if timeout 4 ssh "$VM_HOST" "DISPLAY=$DISPLAY_REMOTE wmctrl -l 2>/dev/null | grep -q 'BTerminal — Terminal'"; then
            sleep 1  # let GTK finish initial layout
            return 0
        fi
        sleep 1
        i=$((i+1))
    done
    echo "ERROR: BT window did not appear within 12s" >&2
    return 2
}

sync_to_vm() {
    for f in "$@"; do
        scp -q "$f" "$VM_HOST:$VM_BTERMINAL/$f"
    done
}

# Take a screenshot, append step entry to actions.log, return host PNG path
shot() {
    local bug="$1" step="$2" action="$3"
    local dir="$SMOKE_ROOT/$bug"
    mkdir -p "$dir"
    local fname=$(printf "%02d_%s.png" "$((10#$step))" "$action")
    local hostpath="$dir/$fname"
    timeout 8 ssh "$VM_HOST" "DISPLAY=$DISPLAY_REMOTE gnome-screenshot -f /tmp/_e2e_shot.png" >/dev/null 2>&1
    scp -q "$VM_HOST:/tmp/_e2e_shot.png" "$hostpath"
    echo "[$(date '+%H:%M:%S')] step=$step action=$action png=$fname" >> "$dir/actions.log"
    echo "$hostpath"
}

# Crop a PNG on VM via PIL, pull cropped version. Useful for Read-tool zoom.
# Default crop covers BT window regardless of position — top-left 900×450 of
# screen captures menu/sidebar even when window is at (10,72) or (369,126).
shot_zoom() {
    local bug="$1" step="$2" action="$3"
    local x1="${4:-0}" y1="${5:-60}" x2="${6:-900}" y2="${7:-510}"
    local dir="$SMOKE_ROOT/$bug"
    mkdir -p "$dir"
    local fname=$(printf "%02d_%s_zoom.png" "$((10#$step))" "$action")
    local hostpath="$dir/$fname"
    timeout 6 ssh "$VM_HOST" "DISPLAY=$DISPLAY_REMOTE gnome-screenshot -f /tmp/_e2e_shot.png && python3 -c '
from PIL import Image
im = Image.open(\"/tmp/_e2e_shot.png\")
crop = im.crop(($x1, $y1, $x2, $y2))
crop.save(\"/tmp/_e2e_zoom.png\")
'" >/dev/null
    scp -q "$VM_HOST:/tmp/_e2e_zoom.png" "$hostpath"
    echo "[$(date '+%H:%M:%S')] step=$step action=$action png=$fname (zoom $x1,$y1-$x2,$y2)" >> "$dir/actions.log"
    echo "$hostpath"
}

# Locate BT window, return "x y w h" geometry
bt_geometry() {
    timeout 4 ssh "$VM_HOST" "DISPLAY=$DISPLAY_REMOTE wmctrl -lG 2>&1 | grep 'BTerminal — Terminal'" | awk '{print $3, $4, $5, $6}'
}

# Click absolute screen coords (with wmctrl activate first to ensure focus)
click_at() {
    local bug="$1" step="$2" action="$3" x="$4" y="$5"
    timeout 6 ssh "$VM_HOST" "DISPLAY=$DISPLAY_REMOTE wmctrl -a 'BTerminal — Terminal' 2>&1; sleep 0.3; DISPLAY=$DISPLAY_REMOTE xdotool mousemove $x $y click 1; sleep 0.5" >/dev/null
    shot_zoom "$bug" "$step" "$action"
}

# Send keypress (xdotool key syntax), then screenshot
key_press() {
    local bug="$1" step="$2" action="$3" keyspec="$4"
    timeout 6 ssh "$VM_HOST" "DISPLAY=$DISPLAY_REMOTE wmctrl -a 'BTerminal — Terminal' 2>&1; sleep 0.2; DISPLAY=$DISPLAY_REMOTE xdotool key $keyspec; sleep 0.5" >/dev/null
    shot_zoom "$bug" "$step" "$action"
}

# Open menubar item by accessibility (AT-SPI) — bypasses flaky mouse
# coords. Requires pyatspi on VM (verified 2026-05-10) + tools/_e2e_atspi_driver.py.
# Falls back to F10+arrow if AT-SPI unavailable.
open_menu() {
    local bug="$1" step="$2" label="$3"
    if timeout 6 ssh "$VM_HOST" "cd $VM_BTERMINAL && DISPLAY=$DISPLAY_REMOTE python3 tools/_e2e_atspi_driver.py click_menu '$label' 2>&1" | grep -q "OK clicked"; then
        sleep 0.5
        shot_zoom "$bug" "$step" "menu_${label}_open"
        return 0
    fi
    # Fallback: F10 + Right×N (legacy path)
    echo "WARN: AT-SPI click failed for $label, falling back to F10+arrows" >&2
    local right_count
    case "$label" in
        Plik|File) right_count=0 ;;
        Widok|View) right_count=1 ;;
        Narzędzia|Tools) right_count=2 ;;
        *) echo "ERROR: unknown menu label $label" >&2; return 2 ;;
    esac
    local cmd="DISPLAY=$DISPLAY_REMOTE wmctrl -a 'BTerminal — Terminal'; sleep 0.3; DISPLAY=$DISPLAY_REMOTE xdotool key F10; sleep 0.4"
    for ((i=0; i<right_count; i++)); do
        cmd="$cmd; DISPLAY=$DISPLAY_REMOTE xdotool key Right; sleep 0.3"
    done
    timeout 8 ssh "$VM_HOST" "$cmd" >/dev/null
    shot_zoom "$bug" "$step" "menu_${label}_open"
}

# Click a menu item under a parent menu via AT-SPI (atspi will open
# parent if not already open). E.g.:
#   click_menu_item bug1-fix 02 select_diagnostics "Narzędzia" "Diagnostyka…"
click_menu_item() {
    local bug="$1" step="$2" action="$3" parent_menu="$4" item="$5"
    timeout 6 ssh "$VM_HOST" "cd $VM_BTERMINAL && DISPLAY=$DISPLAY_REMOTE python3 tools/_e2e_atspi_driver.py click_menu_item '$parent_menu' '$item'" >/dev/null
    sleep 0.5
    shot_zoom "$bug" "$step" "$action"
}

# Click a button by label via AT-SPI (works on dialogs / sidebar
# buttons / etc.). E.g.:
#   click_button bug4-fix 04 click_pull "Pull"
click_button() {
    local bug="$1" step="$2" action="$3" label="$4"
    timeout 6 ssh "$VM_HOST" "cd $VM_BTERMINAL && DISPLAY=$DISPLAY_REMOTE python3 tools/_e2e_atspi_driver.py click_button '$label'" >/dev/null
    sleep 0.5
    shot_zoom "$bug" "$step" "$action"
}

# Diagnostic: dump widget tree to a file in smoke-logs/<bug>/
dump_widget_tree() {
    local bug="$1" step="$2"
    local dir="$SMOKE_ROOT/$bug"
    mkdir -p "$dir"
    local fname=$(printf "%02d_widget_tree.txt" "$((10#$step))")
    timeout 8 ssh "$VM_HOST" "cd $VM_BTERMINAL && DISPLAY=$DISPLAY_REMOTE python3 tools/_e2e_atspi_driver.py dump_tree --max-depth 8" > "$dir/$fname"
    echo "[$(date '+%H:%M:%S')] step=$step action=widget_tree_dump file=$fname" >> "$dir/actions.log"
    echo "$dir/$fname"
}

# Run pin test on VM. Returns 0 if all PASS (no FAIL), 1 otherwise.
pin_test() {
    local test_file="$1"
    timeout 60 ssh "$VM_HOST" "cd $VM_BTERMINAL && DISPLAY=$DISPLAY_REMOTE python3 -m pytest '$test_file' --tb=line -v" 2>&1 | tee /tmp/_pin_test.log
    if grep -q ' failed' /tmp/_pin_test.log; then
        return 1
    fi
    return 0
}

# Build BT i18n catalogs on host then sync .mo to VM
i18n_build_and_sync() {
    ./tools/i18n.sh extract && ./tools/i18n.sh update && ./tools/i18n.sh compile
    scp -q locale/bterminal.pot "$VM_HOST:$VM_BTERMINAL/locale/bterminal.pot"
    for lang_dir in locale/*/LC_MESSAGES; do
        scp -q "$lang_dir/bterminal.po" "$VM_HOST:$VM_BTERMINAL/$lang_dir/bterminal.po" 2>/dev/null
        scp -q "$lang_dir/bterminal.mo" "$VM_HOST:$VM_BTERMINAL/$lang_dir/bterminal.mo" 2>/dev/null
    done
}

# When sourced, just print available functions
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "Source this file (source tools/_e2e_ui_driver.sh) and use:"
    echo "  cleanup_bt"
    echo "  launch_bt <lang>"
    echo "  open_menu <bug-dir> <step-N> <Plik|Widok|Narzędzia>"
    echo "  click_at <bug-dir> <step-N> <action-name> <screen-x> <screen-y>"
    echo "  key_press <bug-dir> <step-N> <action-name> <xdotool-keyspec>"
    echo "  shot <bug-dir> <step-N> <action-name>"
    echo "  shot_zoom <bug-dir> <step-N> <action-name> [x1] [y1] [x2] [y2]"
    echo "  bt_geometry"
    echo "  sync_to_vm <files...>"
    echo "  i18n_build_and_sync"
    echo "  pin_test <test-file>"
fi
