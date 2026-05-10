"""Pin tests for #164 — Release QA Process & Methodology.

Validates the methodology document + sanity script + checklist
template stay in sync. Catches regressions where someone:
  - removes the NON-NEGOTIABLE section
  - skips listing a sub-task
  - adds a new sub-task without updating the doc
  - lets the sanity script accept missing screenshots
"""
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "release-qa-process.md"
TEMPLATE = REPO_ROOT / "smoke-logs" / "release-qa" / "CHECKLIST_TEMPLATE.md"
SANITY = REPO_ROOT / "tools" / "release_qa_sanity.sh"


# ── Methodology document ──────────────────────────────────────────────────


def test_doc_exists():
    assert DOC.is_file()
    assert DOC.stat().st_size > 1000, "doc must have actual content"


def test_doc_has_non_negotiable_rules():
    """Pin: 6 rules from spec + rule #7 (VM-only) — these are the
    contract. Rule #7 added 2026-05-08 after user explicitly demanded
    'testuj w VM'."""
    src = DOC.read_text()
    assert "NON-NEGOTIABLE" in src
    # Each rule is numbered 1-7
    for n in range(1, 8):
        assert f"{n}." in src, f"missing rule #{n}"


def test_doc_rule_7_mandates_vm_execution():
    """Pin: rule #7 says ALL test execution happens on vm-test, NEVER
    on host. Host is only for code edit + ssh control + pin tests.
    Catches regressions where future-tester runs xdotool locally."""
    src = DOC.read_text()
    # The rule must explicitly call out vm-test as canonical target
    assert "vm-test" in src
    # And explicitly forbid host execution
    assert ("NIGDY na hoście" in src or "NEVER on host" in src or
            "Host służy tylko" in src)
    # And explain that pin tests are the only host-side exception
    assert "PIN tests" in src or "pin tests" in src.lower()


def test_doc_lists_all_sub_tasks_165_to_179():
    """Pin: tester needs the full spec table to plan a release run."""
    src = DOC.read_text()
    for n in range(165, 180):
        assert f"#{n}" in src, f"missing sub-task: #{n}"


def test_doc_documents_evidence_folder_structure():
    """Pin: spec mandates `smoke-logs/release-qa/<task-id>/` layout."""
    src = DOC.read_text()
    assert "smoke-logs/release-qa/" in src
    assert "screenshots/" in src
    assert "checklist.md" in src
    assert "install.log" in src


def test_doc_includes_hard_requirements_section():
    """Pin: 3 categories (Instalator / AI Providers / UI) + each item."""
    src = DOC.read_text()
    assert "Hard requirements" in src or "Hard Requirements" in src
    # Instalator section
    assert "rollback" in src.lower()
    assert "flock" in src.lower()
    # AI providers
    assert "Claude" in src and "Copilot" in src and "Aider" in src
    assert "context file" in src.lower() or "CLAUDE.md" in src
    # UI
    assert "menu" in src.lower()
    assert "Sidebar" in src


def test_doc_records_pitfalls_from_e2e_iteration():
    """Pin: bugs found in #157-#163 must be carried into the doc so
    next tester doesn't repeat them."""
    src = DOC.read_text()
    # Sample of pitfalls that MUST be documented
    for pitfall in ("F10", "alt+F4", "force=true", "sleep 999999",
                    "Brak dostępu"):
        assert pitfall in src, f"missing pitfall: {pitfall}"


def test_doc_checklist_template_format_matches_template_file():
    """Pin: doc shows a checklist template; the actual TEMPLATE file
    must mirror the same structure so tester copies-and-fills."""
    template_src = TEMPLATE.read_text()
    doc_src = DOC.read_text()
    # Both must have the same key sections
    for section in ("Cel", "Pre-state", "Kroki", "Acceptance",
                    "Post-state", "Verdict"):
        assert section in template_src, f"template missing: {section}"
        assert section in doc_src, f"doc missing: {section}"


# ── Checklist template ────────────────────────────────────────────────────


def test_template_exists():
    assert TEMPLATE.is_file()


def test_template_has_unticked_items_for_tester_to_fill():
    """Pin: template must START with `[ ]` so tester ticks them.
    Catches accidental commit of pre-ticked template."""
    src = TEMPLATE.read_text()
    unticked = src.count("- [ ]")
    assert unticked >= 5, (
        f"template needs ≥5 unticked items for tester to fill, "
        f"got {unticked}"
    )


def test_template_warns_about_no_tasks_done_until_filled():
    """Pin: spec rule #5(c): no `tasks done` without all evidence.
    Template must remind the tester."""
    src = TEMPLATE.read_text()
    assert "tasks done" in src.lower() or \
           "ticked" in src.lower() or \
           "checked" in src.lower() or \
           "wszystkie sekcje muszą" in src.lower()


# ── Sanity script ─────────────────────────────────────────────────────────


def test_sanity_script_exists_and_executable():
    assert SANITY.is_file()
    assert os.access(SANITY, os.X_OK)


def test_sanity_passes_bash_syntax_check():
    res = subprocess.run(
        ["bash", "-n", str(SANITY)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr


def test_sanity_iterates_all_15_sub_tasks():
    """Pin: script must check each task ID #165-#179."""
    src = SANITY.read_text()
    for n in range(165, 180):
        assert str(n) in src, f"sanity missing task: #{n}"


def test_sanity_blocks_release_on_unchecked_items():
    """Pin: script must grep for `[ ]` (unchecked) and fail when
    found. Otherwise tester's incomplete checklist passes silently."""
    src = SANITY.read_text()
    assert "grep -cE '^- \\[ \\]'" in src, (
        "sanity must count unchecked checklist items"
    )
    assert "RELEASE BLOCKED" in src or "exit 1" in src


def test_sanity_blocks_on_missing_screenshots():
    """Pin: sub-task without ≥1 screenshot >1KB must fail."""
    src = SANITY.read_text()
    assert "find" in src and ".png" in src
    assert "-size +1k" in src or "size >" in src.lower()


def test_sanity_runs_pin_suite_regression():
    """Pin: script must invoke pytest as final gate."""
    src = SANITY.read_text()
    assert "pytest tests/" in src
