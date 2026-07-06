---
name: merge-info-builder
description: Build or update a merge-info.json hover-annotation file for diff-and-merge style hands-on labs. Scans a labs.md for every "code -d completefile skeletonfile" step, diffs each file pair to find the blocks students will merge in, and writes student-friendly AI-generated notes for each block so the Merge Info VS Code extension can show hover info popups during merges. Use this skill whenever the user mentions merge-info.json, merge info annotations, hover notes/annotations for labs, annotating diff-merge steps, or wants explanations generated for the code blocks in their lab's "code -d" merge steps. Also trigger on requests like "build the popups for this repo", "annotate my labs", or "generate hover notes from labs.md". For TechUpSkills / Brent Laster training course repos.
---

# Merge Infos Builder

Generate a cumulative `merge-info.json` for a lab repository so that the
Merge Info VS Code extension can show hover explanations ("info popups")
on each block of code students merge in during `code -d` diff/merge steps.

## Background: how the pieces fit

In these hands-on labs, students run `code -d <complete-file> <skeleton-file>`
and merge the red blocks from the complete file (left side) into the skeleton.
The Merge Info extension reads `merge-info.json` from the repo root and
shows a markdown popup when a student hovers over an annotated block in the
complete file. Your job is to produce that file: one annotation per merge
block, with a note that helps a student understand what they're about to merge
*before* they merge it.

## Workflow

### 1. Locate inputs

Find the `labs.md` file (usually at the repo root; sometimes under `docs/`).
If the user didn't specify a repo and more than one candidate exists, ask.
Treat the directory containing `labs.md` as the repo root unless the file
references paths that only resolve from a parent directory.

### 2. Extract the merge blocks deterministically

Run the bundled script — do not hand-parse labs.md or eyeball diffs:

```bash
python3 <skill-path>/scripts/extract_merge_blocks.py \
  --labs <repo>/labs.md \
  --root <repo> \
  [--existing <repo>/merge-info.json] \
  --out /tmp/merge_blocks.json
```

Pass `--existing` whenever a `merge-info.json` is already present. The
script:

- finds every `code -d <file1> <file2>` occurrence (deduplicating repeats),
- diffs each pair with difflib to find blocks present in file1 but not file2
  (the red blocks students merge in),
- suggests an `anchor` (the block's first non-blank line) plus the extent
  hint (`lines` or `endAnchor`) needed for the extension to resolve the exact
  block,
- marks each block `covered: true` if an existing annotation already resolves
  to it, and carries existing entries through in `existing_entries`.

Read `/tmp/merge_blocks.json` and check `warnings` — missing files or
ambiguous anchors are listed there. Tell the user about any pair that could
not be processed rather than silently skipping it.

### 3. Write notes for uncovered blocks

This is the part that needs you, not a script. For each block with
`covered: false`:

- Read the lab step context: the script includes `lab_context` (the lines of
  labs.md surrounding the `code -d` command). Also read enough of file1 to
  understand the block's role in the whole file.
- Write a `title` (3-6 words, what the block IS) and a `note` built to be
  **scanned in a few seconds, not read as prose**. Labs run on a clock — a
  popup that takes 20 seconds to read steals time from the lab itself. The
  popup is optional depth for students who want it, so the essentials must
  land in the first line:

  - First line: one bold sentence — what the block does and why it's here.
    Aim for 15 words or fewer.
  - Then at most 2-3 short bullets (roughly 10 words each), and only when
    they earn their place: a key term in plain language, a gotcha, or a
    pointer to where the block gets used/tested later in the lab.
  - Simple blocks get the one bold line and nothing else.
  - Keep the whole note under ~45 words. If you can't fit the explanation,
    that's a sign to summarize the intent and point to the lab section that
    explains it, not to write more.

  Don't restate the code line by line — the student can see the code; tell
  them the intent.
- **System prompts and prompt templates get popups too.** Blocks inside a
  string assigned to a variable are real merge material — the script keeps
  them, unlike docstrings. Detection is structural, so it covers any name
  (`SYSTEM_PROMPT`, `SYSTEM_TEMPLATE`, `TARGET_SYSTEM`, ...) and any style:
  same-line `X = """...`, wrapped `X = (` followed by a triple-quote on its
  own line, and f-string templates. Explain the prompt's *role*, not its prose: what
  behavior or persona it sets, what constraints it imposes, and anything
  planted deliberately (canary values, intentional weaknesses the lab will
  attack later, template placeholders and what fills them).
- Note style example (as a JSON `note` array):

  ```json
  "note": [
    "**Regex filters that try to catch common prompt-injection phrasing.**",
    "- One pattern per attack style ('ignore your instructions', persona swaps)",
    "- You'll bypass these later — regex alone isn't enough"
  ]
  ```

### 4. Assemble the cumulative merge-info.json

One file at the repo root covering ALL labs:

- Keys under `files` are repo-relative paths to each complete file (file1).
- Preserve every entry from `existing_entries` **verbatim** — these may be
  hand-tuned. Only append new entries for uncovered blocks. Never rewrite an
  existing note unless the user explicitly asks.
- For each new entry, use the script's suggested `anchor` and extent hint
  as-is unless the block was flagged `ambiguous` — in that case choose a more
  distinctive anchor line within the block (the extension matches the FIRST
  line containing the anchor text, so the anchor must first-match at the
  block's start) and recompute the extent so the range still covers the block.

See `references/merge-info-format.md` for the exact file format the
extension expects.

### 5. Validate and report

Re-run the script with `--existing` pointing at the file you just wrote. Every
block should now report `covered: true` and there should be no new warnings —
this proves each annotation actually resolves to its block. Then give the user
a short summary: files annotated, blocks covered (new vs preserved), and any
blocks you skipped with the reason.

## Edge cases

- **Same file pair in multiple lab steps**: annotate once; merge the lab
  contexts when writing notes.
- **Blocks that are pure whitespace or a lone closing bracket**: the script
  drops whitespace-only blocks; if a surviving block is still too trivial to
  explain (a single `)` line), skip it and say so in the summary.
- **Docstring/comment-only blocks**: the script automatically drops blocks
  whose diff is only docstrings or comments (reported as `doc_only_skipped`
  per pair) — students don't need a popup explaining prose they can read.
  Don't hand-add annotations for these. A docstring INSIDE a code block is
  fine because the block still has code. Note the distinction: string content
  assigned to variables (system prompts, templates) is NOT doc-only — the
  script keeps those blocks and they get annotated like any code merge.
- **file2 missing** (skeleton generated later): the script falls back to
  treating logical top-level chunks of file1 as blocks and flags the pair in
  `warnings`; confirm with the user before annotating a pair like this.
- **No labs.md found**: ask the user for the path — don't guess a different
  markdown file.
