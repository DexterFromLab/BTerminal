# /reflect — Rules reflection after a mistake or inefficiency

Invoked when Claude notices it made a mistake, took a risky action, or spotted a recurring inefficiency. The goal is to prevent repetition by proposing new rules.

## When to invoke

Invoke `/reflect` when you catch yourself having:
- Pushed code / committed without asking
- Made a destructive action (drop table, force push, rm -rf)
- Written code without testing or verifying it
- Made assumptions and not checked them
- Repeated a mistake the user already corrected once
- Taken an action that surprised or disappointed the user
- Noticed a pattern that slows down collaboration

## Flow

### 1. STOP and state the observation

Before anything else, write ONE clear sentence describing what you noticed:

> "STOP — I noticed I [specific action] without [what I should have done first]."

Be specific. Not "I made a mistake" but "I pushed to main without asking for confirmation."

### 2. Resolve project context

Determine the current project name and directory:
- Project name: from the intro prompt context (e.g. `BTerminal`) or `ctx` session lookup
- Project dir: the `project_dir` from the current session config

### 3. Run dry-run wizard

```bash
memory_wizard <project> --project-dir <project_dir> --dry-run
```

Read the output carefully. The wizard may propose rules unrelated to your STOP observation — that is expected.

### 4. Present analysis to user

Structure your message as follows:

---
**STOP — co zauważyłem:**
[Your one-sentence observation from step 1, and brief context of what happened]

**Moja propozycja reguły:**
```
ctx rules add <project> "<rule preventing this from recurring>"
```

**Wizard dodatkowo zaproponował** (ocena):
- ✓ `[rule text]` — [why you think it's relevant to this session]
- ✗ `[rule text]` — [why you think it's not urgent now]

Czy mam dodać zaznaczone reguły?
---

Be objective about wizard proposals. Only present wizard rules that you genuinely believe are relevant to THIS session. Skip generic or low-value proposals.

### 5. Apply approved rules

Wait for user response. If user approves (wholly or partially):

```bash
ctx rules add <project> "rule text"
# repeat for each approved rule
```

Confirm what was added:
> "Dodałem N reguł. Będą aktywne od następnego wstrzyknięcia."

### 6. If no wizard proposals worth presenting

If the wizard returned nothing useful beyond your own observation, just present your one rule and ask for approval. Don't pad with low-quality proposals.

## Key principles

- Your STOP observation always comes first — it is the most reliable signal
- Wizard proposals are secondary input, not gospel
- Only rules that prevent concrete, recurring problems are worth adding
- Do not add rules about things that happened once and are unlikely to repeat
- Rules should be actionable and specific, not vague ("be more careful")
