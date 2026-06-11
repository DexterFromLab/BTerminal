# Contributing to BTerminal

Thanks for your interest in BTerminal! Contributions of every kind are
welcome — and most of them require **no programming and no git knowledge**.
This guide has two tracks: pick the one that fits you.

- [I don't code / I've never used GitHub](#track-1--no-programming-needed)
- [I'm a developer](#track-2--code-contributions)

Questions that don't fit an issue go to
[Discussions](https://github.com/DexterFromLab/BTerminal/discussions).

---

## Track 1 — no programming needed

### Report a bug

All you need is a free [GitHub account](https://github.com/signup).

1. Go to [Issues](https://github.com/DexterFromLab/BTerminal/issues) →
   **New issue**.
2. Describe the problem. The more of the following you include, the faster
   it gets fixed:
   - **BTerminal version** — shown in the window title (e.g. `BTerminal v1.3.5`).
   - **Your Linux distribution** (e.g. Linux Mint 22, Pop!_OS 24.04, Ubuntu 24.04).
   - **AI provider involved**, if any (Claude Code ✨ / Copilot 🤖 / Aider 🦫).
   - **Steps to reproduce** — what you clicked/typed, in order.
   - **What you expected** vs **what actually happened**.
   - A **screenshot** (drag the image straight into the issue text box).
   - For install/update problems: the contents of
     `~/.config/bterminal/install_errors.json`.
   - For translation/UI-text problems: which **language** you use in
     Options → Language.

There are no bad bug reports. "It crashed when I did X" with a screenshot
is already useful.

### Suggest a feature

Open a thread in
[Discussions → Ideas](https://github.com/DexterFromLab/BTerminal/discussions)
and describe the problem you're trying to solve (not only the solution you
imagine). If maintainers pick it up, it becomes an issue.

### Improve a translation (no coding involved)

BTerminal ships in 13 languages; each one lives in a single editable file:
`locale/<code>/LC_MESSAGES/bterminal.po` (e.g. `locale/de/LC_MESSAGES/bterminal.po`).

Without git:

1. Open the `.po` file for your language on GitHub and click
   **Download raw file** (the ⤓ icon).
2. Edit it in [Poedit](https://poedit.net/) (free) — it shows
   "English → your language" pairs; you only fill in the right column.
3. Open a new issue titled `Translation update: <language>` and attach the
   edited file (zip it first — GitHub issues don't accept bare `.po` files).

A maintainer will commit it on your behalf, with credit. Want a **new**
language? Open an issue saying which one — the scaffolding takes one command
on our side, then the steps above apply.

The license dialog is also translated per language
(`defaults/license/LICENSE.<code>.md`); translating it is optional —
missing translations fall back to English.

### Fix documentation from your browser

For any `.md` file (this one, `README.md`, files in `docs/`):

1. Open the file on GitHub and click the **pencil icon** (Edit).
2. Make your change in the browser editor.
3. Click **Commit changes…** → "Create a new branch and start a pull
   request" → **Propose changes**. GitHub handles the git mechanics
   (including forking) automatically.

### Spread the word

Star the repository, show BTerminal to colleagues, post screenshots or
short demos. For a project this size, every mention matters.

---

## Track 2 — code contributions

### Prerequisites

Linux with GTK 3, Python 3.10+, Node.js 22+ (see
[README → Requirements](README.md#requirements)).

### Setup

```bash
git clone https://github.com/DexterFromLab/BTerminal.git
cd BTerminal
./install.sh
```

When iterating on BTerminal's own code, use the two-tier deploy described in
[README → Development workflow](README.md#development-workflow):
`tools/sync_install.sh` for fast host deploys, `tools/vm_test.sh` for
isolated VM regression.

### Tests

Run before every pull request:

```bash
pip install pytest httpx Pillow      # once
sudo apt install xvfb                # once, for component/e2e layers
./tools/test_all.sh --quick          # unit layer, sub-second
./tools/test_all.sh                  # fast suite — minimum bar for a PR
```

A PR that adds a feature or fixes a bug should add a test pinning the new
behavior — that's the project convention (every fixed bug gets a pin test).

### Code conventions

- **i18n**: every user-facing UI string must be wrapped in `_()` / `N_()`.
  `tools/check_i18n.py` (part of the test suite) fails the build otherwise.
  After adding strings, run `./tools/i18n.sh extract && ./tools/i18n.sh update`.
- **English-only by policy**: `README.md`, `errata.json`, installer output,
  and AI intro prompts stay in English. UI strings are translated via gettext.
- **Commit messages**: Conventional Commits, as in the existing history —
  `feat(tasks): …`, `fix(sudo): …`, `docs(readme): …`, `test(e2e): …`,
  `chore: …`, `refactor: …`.

### Pull request workflow

1. Branch from `master`: `git checkout -b <type>/<short-description>`
   (e.g. `fix/options-scrollbar`, `docs/contributing-guide`).
   No write access? Fork first, then branch in your fork.
2. Keep PRs small and focused — one topic per PR.
3. For UI changes, attach a before/after screenshot.
4. Make sure `./tools/test_all.sh` passes.
5. Push and open a pull request against `master`; describe **what** changed
   and **why**.

### Files you should not touch in a regular PR

- `VERSION` and `errata.json` — these drive the in-app auto-updater and are
  bumped by the maintainer when cutting a release.
- `LICENSE.md` and `defaults/license/` — license changes are
  maintainer-only.

Note that `README.md`, `defaults/` and `VERSION` are live-symlinked into
every user's installation: whatever lands on `master` reaches users on
their next `git pull`/update, without a reinstall. Review accordingly.

---

## License

BTerminal uses a custom attribution-required license
([LICENSE.md](LICENSE.md), v1.1). By submitting a contribution you agree
that it is provided under the same license. The authorship/attribution
information described in the license must remain intact in all
contributions and derivative works.
