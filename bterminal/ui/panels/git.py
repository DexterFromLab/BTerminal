"""GitPanel — right-side accordion with git status, branches, commits

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/panels/panel_git.py` in a later migration etap.
"""

import datetime
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from bterminal.config import (
    CATPPUCCIN,
    CONFIG_DIR,
    CLAUDE_SESSIONS_FILE,
    CONSULT_CONFIG_FILE,
    OPTIONS_FILE,
    PLUGINS_CONFIG_FILE,
    PLUGINS_DIR,
    SESSIONS_FILE,
    _OPTIONS,
    _parse_color,
    _session_color,
    show_error_dialog,
    show_info_dialog,
)


class GitPanel(Gtk.Box):
    """Right-side panel with accordion Git sections and file monitoring."""

    _REFRESH_INTERVAL_MS = 3000  # auto-refresh every 3s when visible

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self._git_dir = None
        self._monitors = []
        self._timer_id = None
        self.get_style_context().add_class("sidebar")
        self.get_style_context().add_class("git-panel")
        self.set_size_request(0, -1)

        # ── Header bar ──
        header = Gtk.Box(spacing=6)
        header.get_style_context().add_class("git-header")

        self._btn_toggle = Gtk.Button(label="▶")
        self._btn_toggle.get_style_context().add_class("sidebar-btn")
        self._btn_toggle.set_tooltip_text("Hide Git panel (Ctrl+G)")
        self._btn_toggle.connect("clicked", lambda _: self.app.toggle_git_panel())
        header.pack_start(self._btn_toggle, False, False, 0)

        lbl = Gtk.Label(label="Git")
        lbl.set_xalign(0)
        header.pack_start(lbl, True, True, 0)

        self._btn_refresh = Gtk.Button(label="⟳")
        self._btn_refresh.get_style_context().add_class("sidebar-btn")
        self._btn_refresh.set_tooltip_text("Refresh (F5)")
        self._btn_refresh.connect("clicked", lambda _: self.refresh())
        header.pack_end(self._btn_refresh, False, False, 0)

        self.pack_start(header, False, False, 0)

        # ── Scrollable content ──
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll.add(self._content)
        self.pack_start(scroll, True, True, 0)

        # "No git" placeholder
        self._no_git_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._no_git_box.set_margin_top(40)
        self._no_git_box.set_margin_start(16)
        self._no_git_box.set_margin_end(16)
        self._no_git_lbl = Gtk.Label()
        self._no_git_lbl.set_line_wrap(True)
        self._no_git_lbl.set_justify(Gtk.Justification.CENTER)
        self._no_git_box.pack_start(self._no_git_lbl, False, False, 0)
        self._btn_init = Gtk.Button(label="  git init  ")
        self._btn_init.get_style_context().add_class("sidebar-btn")
        self._btn_init.set_halign(Gtk.Align.CENTER)
        self._btn_init.connect("clicked", self._on_git_init)
        self._no_git_box.pack_start(self._btn_init, False, False, 0)
        self._content.pack_start(self._no_git_box, False, False, 0)

        # ── Sections ──
        self._sections_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._content.pack_start(self._sections_box, True, True, 0)

        # Branch
        self._branch_label = Gtk.Label()
        self._branch_label.set_xalign(0)
        self._branch_label.set_line_wrap(True)
        self._branch_label.set_selectable(True)
        self._add_section("Branch", self._branch_label)

        # Changes
        changes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._changes_summary = Gtk.Label()
        self._changes_summary.set_xalign(0)
        changes_box.pack_start(self._changes_summary, False, False, 0)
        # File list
        self._changes_store = Gtk.ListStore(str, str, str)
        self._changes_tree = Gtk.TreeView(model=self._changes_store)
        self._changes_tree.set_headers_visible(False)
        self._changes_tree.set_enable_search(False)
        r_status = Gtk.CellRendererText()
        c_status = Gtk.TreeViewColumn("", r_status, markup=0)
        c_status.set_min_width(24)
        c_status.set_max_width(28)
        self._changes_tree.append_column(c_status)
        r_file = Gtk.CellRendererText()
        r_file.set_property("ellipsize", Pango.EllipsizeMode.START)
        c_file = Gtk.TreeViewColumn("", r_file, text=1)
        c_file.set_expand(True)
        c_file.set_min_width(0)
        self._changes_tree.append_column(c_file)
        r_diff = Gtk.CellRendererText()
        c_diff = Gtk.TreeViewColumn("", r_diff, markup=2)
        c_diff.set_min_width(0)
        self._changes_tree.append_column(c_diff)
        ch_scroll = Gtk.ScrolledWindow()
        ch_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        ch_scroll.set_min_content_height(60)
        ch_scroll.set_max_content_height(180)
        ch_scroll.set_propagate_natural_height(True)
        ch_scroll.add(self._changes_tree)
        changes_box.pack_start(ch_scroll, True, True, 0)
        self._sec_changes = self._add_section("Changes", changes_box)

        # Stash
        self._stash_label = Gtk.Label()
        self._stash_label.set_xalign(0)
        self._stash_label.set_line_wrap(True)
        self._stash_label.set_selectable(True)
        self._sec_stash = self._add_section("Stash", self._stash_label)

        # LFS / Binary
        lfs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._lfs_label = Gtk.Label()
        self._lfs_label.set_xalign(0)
        self._lfs_label.set_line_wrap(True)
        self._lfs_label.set_selectable(True)
        lfs_box.pack_start(self._lfs_label, False, False, 0)
        self._btn_setup_lfs = Gtk.Button(label="  Setup Git LFS  ")
        self._btn_setup_lfs.get_style_context().add_class("sidebar-btn")
        self._btn_setup_lfs.set_halign(Gtk.Align.START)
        self._btn_setup_lfs.connect("clicked", self._on_setup_lfs)
        self._btn_setup_lfs.set_no_show_all(True)
        lfs_box.pack_start(self._btn_setup_lfs, False, False, 0)
        self._sec_lfs = self._add_section("LFS / Binary", lfs_box)

        # Activity
        self._activity_label = Gtk.Label()
        self._activity_label.set_xalign(0)
        self._activity_label.set_line_wrap(True)
        self._activity_label.set_selectable(True)
        self._add_section("Activity", self._activity_label)

        # Log (last — fills remaining space)
        self._log_view = Gtk.TextView()
        self._log_view.set_editable(False)
        self._log_view.set_cursor_visible(False)
        self._log_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self._log_view.set_monospace(True)
        self._log_view.set_left_margin(4)
        self._log_view.set_right_margin(4)
        self._log_view.set_top_margin(2)
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_min_content_height(100)
        log_scroll.add(self._log_view)
        self._add_section("Log", log_scroll, expand=True)

    def _add_section(self, title, content_widget, expand=False):
        """Add a collapsible section with styled title bar. Returns the outer box."""
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Title bar (clickable to toggle)
        title_btn = Gtk.Button()
        title_btn.set_relief(Gtk.ReliefStyle.NONE)
        title_lbl = Gtk.Label()
        title_lbl.set_markup(f"<small><b>▾ {title}</b></small>")
        title_lbl.set_xalign(0)
        title_btn.add(title_lbl)
        title_btn.get_style_context().add_class("git-section-title")
        outer.pack_start(title_btn, False, False, 0)

        # Body
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.get_style_context().add_class("git-section-body")
        body.pack_start(content_widget, True, True, 0)
        outer.pack_start(body, expand, expand, 0)

        # Toggle collapse
        def _toggle(_btn):
            if body.get_visible():
                body.hide()
                title_lbl.set_markup(f"<small><b>▸ {title}</b></small>")
            else:
                body.show_all()
                title_lbl.set_markup(f"<small><b>▾ {title}</b></small>")
        title_btn.connect("clicked", _toggle)

        self._sections_box.pack_start(outer, expand, expand, 0)
        return outer

    # ── Git commands ──

    def _git(self, *args, timeout=5):
        """Run a git command in the current git dir. Returns stdout or None."""
        if not self._git_dir:
            return None
        try:
            r = subprocess.run(
                ["git"] + list(args),
                cwd=self._git_dir, capture_output=True, text=True, timeout=timeout,
            )
            return r.stdout if r.returncode == 0 else None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

    def _is_git_repo(self):
        return self._git("rev-parse", "--is-inside-work-tree") is not None

    # ── Public API ──

    def set_project_dir(self, path):
        """Set (or clear) the git directory and refresh."""
        new_dir = path if path and os.path.isdir(path) else None
        if new_dir == self._git_dir:
            return
        self._git_dir = new_dir
        self._setup_monitors()
        self.refresh()

    def refresh(self):
        """Reload all git data."""
        is_repo = self._is_git_repo() if self._git_dir else False

        if not is_repo:
            self._sections_box.hide()
            self._no_git_box.show_all()
            if self._git_dir:
                self._no_git_lbl.set_text(f"Not a git repo:\n{self._git_dir}")
            else:
                self._no_git_lbl.set_text(
                    "No project selected.\n"
                    "Open a Claude Code session\nwith project dir.")
            return

        self._no_git_box.hide()
        self._sections_box.show_all()
        # Re-hide LFS button (has no_show_all)
        if not self._btn_setup_lfs.get_visible():
            self._btn_setup_lfs.hide()

        self._refresh_branch()
        self._refresh_changes()
        self._refresh_log()
        self._refresh_stash()
        self._refresh_lfs()
        self._refresh_activity()

    def _refresh_branch(self):
        # Current branch
        branch = (self._git("branch", "--show-current") or "").strip()
        if not branch:
            branch = (self._git("rev-parse", "--short", "HEAD") or "detached").strip()

        c_grn, c_red, c_dim = CATPPUCCIN["green"], CATPPUCCIN["red"], CATPPUCCIN["overlay0"]
        lines = [f"<span size='large' foreground='{c_grn}'><b>{branch}</b></span>"]

        # Upstream tracking
        upstream = self._git("rev-parse", "--abbrev-ref", "@{upstream}")
        if upstream:
            upstream = upstream.strip()
            ahead = (self._git("rev-list", "--count", f"{upstream}..HEAD") or "0").strip()
            behind = (self._git("rev-list", "--count", f"HEAD..{upstream}") or "0").strip()
            parts = []
            if ahead != "0":
                parts.append(f"<span foreground='{c_grn}'>↑{ahead}</span>")
            if behind != "0":
                parts.append(f"<span foreground='{c_red}'>↓{behind}</span>")
            if parts:
                lines.append(f"{' '.join(parts)}  vs  {upstream}")
            else:
                lines.append(f"✓ up to date with {upstream}")

        # Remotes
        remotes = (self._git("remote", "-v") or "").strip()
        if remotes:
            seen = set()
            for line in remotes.splitlines():
                name = line.split()[0]
                if name not in seen:
                    seen.add(name)
                    url = line.split()[1] if len(line.split()) > 1 else ""
                    lines.append(f"<small><span foreground='{c_dim}'>{name}  {url}</span></small>")

        self._branch_label.set_markup("\n".join(lines))

    def _refresh_changes(self):
        self._changes_store.clear()
        status = self._git("status", "--porcelain=v1") or ""
        files = status.rstrip().splitlines() if status.rstrip() else []
        total_add, total_del = 0, 0
        c_grn = CATPPUCCIN["green"]
        c_red = CATPPUCCIN["red"]
        c_yel = CATPPUCCIN["yellow"]

        numstat = {}
        for diff_args in [("diff", "--numstat", "HEAD")]:
            raw = self._git(*diff_args) or ""
            for line in raw.strip().splitlines():
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    a = parts[0] if parts[0] != "-" else "bin"
                    d = parts[1] if parts[1] != "-" else "bin"
                    fname = parts[2]
                    if fname in numstat and numstat[fname] != ("bin", "bin"):
                        pa, pd = numstat[fname]
                        a = str(int(pa) + int(a)) if pa.isdigit() and a.isdigit() else a
                        d = str(int(pd) + int(d)) if pd.isdigit() and d.isdigit() else d
                    numstat[fname] = (a, d)

        for f in files:
            if len(f) < 4:
                continue
            st = f[:2]
            fname = f[3:]
            adds, dels = numstat.get(fname, ("", ""))
            if adds.isdigit():
                total_add += int(adds)
            if dels.isdigit():
                total_del += int(dels)
            stat_str = ""
            if adds or dels:
                parts = []
                if adds and adds != "0":
                    parts.append(f"<span foreground='{c_grn}'>+{adds}</span>")
                if dels and dels != "0":
                    parts.append(f"<span foreground='{c_red}'>-{dels}</span>")
                if adds == "bin":
                    parts = [f"<span foreground='{c_yel}'>bin</span>"]
                stat_str = " ".join(parts)
            s = st.strip()
            status_colors = {
                "M": c_yel, "A": c_grn, "D": c_red,
                "R": CATPPUCCIN["blue"], "C": CATPPUCCIN["blue"],
                "U": c_red, "??": CATPPUCCIN["overlay0"],
            }
            color = status_colors.get(s, CATPPUCCIN["text"])
            st_markup = f"<span foreground='{color}'><b>{s}</b></span>"
            self._changes_store.append([st_markup, fname, stat_str])

        n = len(files)
        if n:
            self._changes_summary.set_markup(
                f"{n} file{'s' if n != 1 else ''}  "
                f"<span foreground='{c_grn}'><b>+{total_add}</b></span>  "
                f"<span foreground='{c_red}'><b>-{total_del}</b></span>"
            )
        else:
            self._changes_summary.set_markup(
                f"<span foreground='{c_grn}'>✓ Working tree clean</span>")

    # ANSI color code → Catppuccin palette mapping for git log
    @staticmethod
    def _ansi_colors():
        return {
            "31": CATPPUCCIN["red"], "32": CATPPUCCIN["green"],
            "33": CATPPUCCIN["yellow"], "34": CATPPUCCIN["blue"],
            "35": CATPPUCCIN["pink"], "36": CATPPUCCIN["teal"],
            "1;31": CATPPUCCIN["red"], "1;32": CATPPUCCIN["green"],
            "1;33": CATPPUCCIN["yellow"], "1;34": CATPPUCCIN["blue"],
            "1;35": CATPPUCCIN["pink"], "1;36": CATPPUCCIN["teal"],
            "1": CATPPUCCIN["text"],
        }

    def _refresh_log(self):
        log = self._git(
            "log", "--oneline", "--decorate", "--graph", "--color=always", "-40",
            timeout=10,
        ) or "(no commits)"
        # Fresh buffer to get clean tags for current theme
        buf = Gtk.TextBuffer()
        ansi = self._ansi_colors()
        tags = {}
        end_iter = buf.get_end_iter
        for line in log.rstrip().splitlines():
            parts = re.split(r'\x1b\[([0-9;]*)m', line)
            current_tag = None
            for i, part in enumerate(parts):
                if i % 2 == 1:
                    if part == "0" or part == "":
                        current_tag = None
                    elif part in ansi:
                        if part not in tags:
                            tag = buf.create_tag(None, foreground=ansi[part])
                            if part.startswith("1"):
                                tag.set_property("weight", Pango.Weight.BOLD)
                            tags[part] = tag
                        current_tag = tags[part]
                else:
                    if part:
                        if current_tag:
                            buf.insert_with_tags(end_iter(), part, current_tag)
                        else:
                            buf.insert(end_iter(), part)
            buf.insert(end_iter(), "\n")
        self._log_view.set_buffer(buf)

    def _refresh_stash(self):
        stash = (self._git("stash", "list") or "").strip()
        if stash:
            self._stash_label.set_text(stash)
            self._sec_stash.show_all()
        else:
            self._sec_stash.hide()

    def _refresh_lfs(self):
        lines = []
        # Check if LFS is installed
        lfs_installed = shutil.which("git-lfs") is not None
        lfs_active = False
        gitattr_path = os.path.join(self._git_dir, ".gitattributes") if self._git_dir else None

        if gitattr_path and os.path.isfile(gitattr_path):
            try:
                with open(gitattr_path) as f:
                    content = f.read()
                if "filter=lfs" in content:
                    lfs_active = True
                    tracked = [
                        l.split()[0] for l in content.splitlines()
                        if "filter=lfs" in l
                    ]
                    lines.append(f"LFS tracking: {', '.join(tracked)}")
            except OSError:
                pass

        # Find large/binary tracked files
        big_files = []
        ls_out = self._git("ls-files", "-z") or ""
        if ls_out:
            for fname in ls_out.split("\0"):
                if not fname:
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
                           ".mp4", ".mp3", ".zip", ".tar", ".gz", ".bin",
                           ".pdf", ".psd", ".ai", ".sketch", ".fig",
                           ".woff", ".woff2", ".ttf", ".otf", ".ico",
                           ".so", ".dll", ".dylib", ".exe"):
                    fpath = os.path.join(self._git_dir, fname)
                    try:
                        sz = os.path.getsize(fpath)
                        if sz > 50_000:  # >50KB
                            big_files.append((fname, sz))
                    except OSError:
                        pass

        if big_files:
            lines.append("")
            lines.append(f"Binary files tracked ({len(big_files)}):")
            for fname, sz in big_files[:10]:
                h = f"{sz / 1024:.0f}KB" if sz < 1_000_000 else f"{sz / 1_000_000:.1f}MB"
                lines.append(f"  {fname} ({h})")
            if len(big_files) > 10:
                lines.append(f"  ... +{len(big_files) - 10} more")

        if not lfs_installed:
            lines.insert(0, "Git LFS: not installed")
            self._btn_setup_lfs.hide()
        elif not lfs_active and big_files:
            lines.insert(0, "Git LFS: not configured (recommended)")
            self._btn_setup_lfs.show()
        elif lfs_active:
            lines.insert(0, "Git LFS: active")
            self._btn_setup_lfs.hide()
        else:
            lines.insert(0, "Git LFS: not needed")
            self._btn_setup_lfs.hide()

        self._lfs_label.set_text("\n".join(lines) if lines else "No binary issues")

    def _refresh_activity(self):
        lines = []
        # Commits last 7 days
        week = self._git("rev-list", "--count", "--since=7 days ago", "HEAD")
        month = self._git("rev-list", "--count", "--since=30 days ago", "HEAD")
        total = self._git("rev-list", "--count", "HEAD")

        if week is not None:
            lines.append(f"Last 7 days: {week.strip()} commits")
        if month is not None:
            lines.append(f"Last 30 days: {month.strip()} commits")
        if total is not None:
            lines.append(f"Total: {total.strip()} commits")

        # Top authors last 30 days
        shortlog = self._git("shortlog", "-sn", "--since=30 days ago", "HEAD")
        if shortlog and shortlog.strip():
            lines.append("")
            lines.append("Authors (30d):")
            for line in shortlog.strip().splitlines()[:5]:
                lines.append(f"  {line.strip()}")

        # Tags
        tags = self._git("tag", "--sort=-creatordate", "-l")
        if tags and tags.strip():
            tag_list = tags.strip().splitlines()[:5]
            lines.append("")
            lines.append(f"Tags ({len(tags.strip().splitlines())} total):")
            for t in tag_list:
                lines.append(f"  {t.strip()}")

        self._activity_label.set_text("\n".join(lines) if lines else "No activity data")

    # ── File monitoring ──

    def _setup_monitors(self):
        """Watch .git dir and working tree for changes (Gio.FileMonitor)."""
        self._stop_monitors()
        if not self._git_dir:
            return
        git_internal = os.path.join(self._git_dir, ".git")
        paths_to_watch = []
        if os.path.isdir(git_internal):
            # Watch .git/HEAD, .git/index for branch/stage changes
            for name in ("HEAD", "index", "refs"):
                p = os.path.join(git_internal, name)
                if os.path.exists(p):
                    paths_to_watch.append(p)
        for p in paths_to_watch:
            try:
                gfile = Gio.File.new_for_path(p)
                if os.path.isdir(p):
                    mon = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
                else:
                    mon = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
                mon.connect("changed", self._on_fs_changed)
                self._monitors.append(mon)
            except GLib.Error:
                pass
        # Periodic fallback (catches working tree changes)
        self._timer_id = GLib.timeout_add(self._REFRESH_INTERVAL_MS, self._on_timer)

    def _stop_monitors(self):
        for m in self._monitors:
            m.cancel()
        self._monitors.clear()
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _on_fs_changed(self, monitor, file, other_file, event_type):
        """Debounced refresh on filesystem change."""
        if not hasattr(self, "_fs_pending"):
            self._fs_pending = False
        if not self._fs_pending:
            self._fs_pending = True
            GLib.timeout_add(500, self._on_fs_debounce)

    def _on_fs_debounce(self):
        self._fs_pending = False
        if self.get_visible():
            self.refresh()
        return False

    def _on_timer(self):
        if self.get_visible() and self._git_dir:
            self.refresh()
        return True  # keep running

    # ── Actions ──

    def _on_git_init(self, _btn):
        if not self._git_dir:
            return
        dlg = Gtk.MessageDialog(
            transient_for=self.app, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Initialize git repository?",
        )
        dlg.format_secondary_text(f"This will run 'git init' in:\n{self._git_dir}")
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.YES:
            return
        try:
            subprocess.run(
                ["git", "init"], cwd=self._git_dir,
                capture_output=True, timeout=10,
            )
            # Auto-create .gitignore with sensible defaults
            gi_path = os.path.join(self._git_dir, ".gitignore")
            if not os.path.exists(gi_path):
                with open(gi_path, "w") as f:
                    f.write(
                        "# Binary & generated\n"
                        "*.pyc\n__pycache__/\n*.o\n*.so\n"
                        "node_modules/\ndist/\nbuild/\n"
                        ".env\n.env.*\n"
                        "# Images (use Git LFS for large assets)\n"
                        "# *.png\n# *.jpg\n"
                        "copied_images/\n"
                    )
            self._setup_monitors()
            self.refresh()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    def _on_setup_lfs(self, _btn):
        if not self._git_dir:
            return
        try:
            subprocess.run(
                ["git", "lfs", "install"], cwd=self._git_dir,
                capture_output=True, timeout=10,
            )
            # Track common binary patterns
            for pattern in ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp",
                            "*.pdf", "*.zip", "*.tar.gz", "*.mp4", "*.mp3",
                            "*.woff", "*.woff2", "*.ttf"]:
                subprocess.run(
                    ["git", "lfs", "track", pattern], cwd=self._git_dir,
                    capture_output=True, timeout=5,
                )
            self.refresh()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    def destroy(self):
        self._stop_monitors()
        super().destroy()


