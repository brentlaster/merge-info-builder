#!/usr/bin/env python3
"""Extract merge blocks from a lab repo's `code -d` steps.

Parses labs.md for `code -d <file1> <file2>` commands, diffs each pair, and
emits the blocks present in file1 (complete) but not file2 (skeleton) — the
red blocks students merge in. For each block it suggests an anchor + extent
matching the Merge Info extension's resolution rules, and marks blocks
already covered by an existing merge-info.json.

Output JSON shape:
{
  "pairs": [
    {
      "file1": "extra/foo_complete.py",   # repo-relative key for merge-info.json
      "file2": "foo.py",
      "lab_context": "...surrounding labs.md text...",
      "blocks": [
        {
          "start_line": 10, "end_line": 19,        # 1-based, inclusive, in file1
          "text": "...block text...",
          "anchor": "HIJACK_PATTERNS = [",
          "extent": {"endAnchor": "]"} | {"lines": 5} | null,
          "ambiguous": false,
          "covered": false
        }
      ]
    }
  ],
  "existing_entries": { ...files map from --existing, passed through verbatim... },
  "warnings": ["..."]
}
"""

import argparse
import difflib
import json
import os
import re
import sys

CODE_D_RE = re.compile(r"code\s+(?:-d|--diff)\s+([^\s`\"']+)\s+([^\s`\"']+)")


def read_lines(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def find_pairs(labs_lines):
    """Return [(file1, file2, context_str, line_no)] for each code -d step."""
    pairs = []
    for i, line in enumerate(labs_lines):
        m = CODE_D_RE.search(line)
        if m:
            ctx_start = max(0, i - 8)
            ctx_end = min(len(labs_lines), i + 3)
            context = "\n".join(labs_lines[ctx_start:ctx_end])
            pairs.append((m.group(1), m.group(2), context, i + 1))
    return pairs


def resolve(root, labs_dir, rel):
    """Try to resolve a lab-file path against repo root, then labs.md dir."""
    for base in (root, labs_dir):
        p = os.path.normpath(os.path.join(base, rel))
        if os.path.isfile(p):
            return p
    return None


def diff_blocks(lines1, lines2):
    """Blocks of file1 lines that are not in file2 (replace/delete opcodes)."""
    sm = difflib.SequenceMatcher(None, lines1, lines2, autojunk=False)
    blocks = []
    for tag, i1, i2, _j1, _j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            blocks.append((i1, i2 - 1))  # 0-based inclusive
    return blocks


def trim_block(lines1, start, end):
    """Drop leading/trailing blank lines; return None if nothing remains."""
    while start <= end and not lines1[start].strip():
        start += 1
    while end >= start and not lines1[end].strip():
        end -= 1
    if start > end:
        return None
    return (start, end)


def default_extent_end(lines1, anchor_line):
    """Where the extension's default rule (run to next blank line) ends."""
    i = anchor_line
    while i + 1 < len(lines1) and lines1[i + 1].strip():
        i += 1
    return i


def first_match_line(lines1, needle):
    for i, line in enumerate(lines1):
        if needle in line:
            return i
    return -1


def triple_quote_states(lines):
    """For each line, the state it STARTS in: None, or a tuple
    (quote, is_docstring) for an open triple-quoted string.

    is_docstring distinguishes documentation strings from *meaningful*
    string content like system prompts and prompt templates (SYSTEM_PROMPT,
    SYSTEM_TEMPLATE, and any other assigned string — detection is structural,
    not name-based). A triple-quote is CONTENT, not a docstring, when:
    - code precedes it on the same line (`SYSTEM_PROMPT = \"\"\"`,
      `return f\"\"\"`, an f/r prefix), or
    - it starts its line but the previous non-blank line ends with an
      assignment/continuation character (= ( [ { , or backslash), which
      covers the wrapped style:
          SYSTEM_TEMPLATE = (
              \"\"\"You are ...\"\"\"
          )
    Only a line-start triple-quote in statement position (module top, after
    a def/class line, etc.) counts as a docstring — blocks inside those are
    prose to skip; blocks inside content strings are real merge material."""
    states = []
    in_q = None  # None or (quote, is_docstring)
    prev_nonblank_end = ""  # last char of the previous non-blank code line
    CONTINUATION = ("=", "(", "[", "{", ",", "\\")
    for line in lines:
        states.append(in_q)
        s, i = line, 0
        opened_here = False
        while True:
            if in_q:
                j = s.find(in_q[0], i)
                if j == -1:
                    break
                i, in_q = j + 3, None
            else:
                cands = [(s.find(q, i), q) for q in ('"""', "'''")]
                cands = [(j, q) for j, q in cands if j != -1]
                h = s.find("#", i)
                if cands and (h == -1 or min(cands)[0] < h):
                    j, q = min(cands)
                    line_start = s[:j].strip() == ""  # only indent before it
                    is_doc = line_start and not prev_nonblank_end.endswith(CONTINUATION)
                    opened_here = True
                    # closed on the same line?
                    close = s.find(q, j + 3)
                    if close == -1:
                        in_q = (q, is_doc)
                        i = j + 3
                    else:
                        i = close + 3
                else:
                    break  # rest of line is comment or has no quotes
        if in_q is None and line.strip() and not line.strip().startswith("#"):
            prev_nonblank_end = line.strip()
        elif opened_here and line.strip():
            prev_nonblank_end = line.strip()
    return states


def is_doc_only(block_lines, start_state=None):
    """True if every non-blank line is a comment or docstring text.

    Doc-only blocks (docstring edits, comment tweaks) are not worth a hover
    popup — students merging them don't need an explanation of prose.
    Handles #-comments, //-comments, /* */ blocks, and triple-quoted strings.
    start_state (from triple_quote_states) marks blocks that begin inside an
    already-open triple-quoted string. If that string is a DOCSTRING the
    prose is skippable; if it's meaningful content (a system prompt or
    template assigned to a variable), the block is real merge material and
    must be kept. A docstring inside a code block doesn't count either way —
    the block still contains code, so it's kept.
    """
    if start_state and not start_state[1]:
        return False  # inside a system prompt / template string — keep it
    in_triple = start_state[0] if start_state else None
    in_cblock = False  # inside /* */
    for raw in block_lines:
        line = raw.strip()
        if not line:
            continue
        if in_triple:
            if in_triple in line:
                in_triple = None
            continue
        if in_cblock:
            if "*/" in line:
                in_cblock = False
            continue
        if line.startswith("#") or line.startswith("//"):
            continue
        if line.startswith("/*"):
            if "*/" not in line:
                in_cblock = True
            continue
        for q in ('"""', "'''"):
            if line.startswith(q):
                if line.count(q) < 2:  # not closed on the same line
                    in_triple = q
                break
        else:
            return False  # a real code line
    return True


def suggest_annotation(lines1, start, end):
    """Suggest anchor + extent replicating the extension's resolution rules.

    Returns (anchor, extent_dict_or_None, ambiguous).
    ambiguous=True means the suggested anchor's first match in file1 is NOT
    the block start, so the extension would attach the popup elsewhere.
    """
    anchor = lines1[start].strip()
    ambiguous = first_match_line(lines1, anchor) != start

    if default_extent_end(lines1, start) == end:
        extent = None
    else:
        end_text = lines1[end].strip()
        # endAnchor works if the first match strictly after the anchor line
        # is exactly the block's end line.
        end_match = -1
        for i in range(start + 1, len(lines1)):
            if end_text and end_text in lines1[i]:
                end_match = i
                break
        if end_match == end:
            extent = {"endAnchor": end_text}
        else:
            extent = {"lines": end - start + 1}
    return anchor, extent, ambiguous


def resolve_entry_range(lines1, entry):
    """Resolve an existing merge-info entry to (start, end) like the
    extension does, or None if the anchor doesn't match."""
    anchor = entry.get("anchor")
    if not anchor:
        return None
    start = first_match_line(lines1, anchor)
    if start == -1:
        return None
    if isinstance(entry.get("lines"), int) and entry["lines"] > 0:
        end = min(start + entry["lines"] - 1, len(lines1) - 1)
    elif entry.get("endAnchor"):
        end = start
        for i in range(start + 1, len(lines1)):
            if entry["endAnchor"] in lines1[i]:
                end = i
                break
    else:
        end = default_extent_end(lines1, start)
    return (start, end)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labs", required=True, help="Path to labs.md")
    ap.add_argument("--root", help="Repo root (default: labs.md directory)")
    ap.add_argument("--existing", help="Existing merge-info.json to preserve")
    ap.add_argument("--out", required=True, help="Output JSON path")
    args = ap.parse_args()

    labs_path = os.path.abspath(args.labs)
    labs_dir = os.path.dirname(labs_path)
    root = os.path.abspath(args.root) if args.root else labs_dir

    warnings = []
    existing = {"files": {}}
    if args.existing:
        try:
            with open(args.existing, encoding="utf-8") as f:
                existing = json.load(f)
                existing.setdefault("files", {})
        except FileNotFoundError:
            warnings.append(f"--existing file not found: {args.existing}")
        except json.JSONDecodeError as e:
            print(f"ERROR: could not parse {args.existing}: {e}", file=sys.stderr)
            sys.exit(1)

    labs_lines = read_lines(labs_path)
    raw_pairs = find_pairs(labs_lines)
    if not raw_pairs:
        warnings.append("No 'code -d <file1> <file2>' steps found in labs.md")

    # Deduplicate pairs, merging lab contexts.
    seen = {}
    order = []
    for f1, f2, ctx, line_no in raw_pairs:
        key = (f1, f2)
        if key in seen:
            seen[key]["lab_context"] += f"\n\n--- (also used near labs.md line {line_no}) ---\n{ctx}"
        else:
            seen[key] = {"file1": f1, "file2": f2, "lab_context": ctx,
                         "labs_md_line": line_no}
            order.append(key)

    result_pairs = []
    for key in order:
        info = seen[key]
        p1 = resolve(root, labs_dir, info["file1"])
        p2 = resolve(root, labs_dir, info["file2"])
        if not p1:
            warnings.append(f"file1 not found, pair skipped: {info['file1']} "
                            f"(labs.md line {info['labs_md_line']})")
            continue
        lines1 = read_lines(p1)
        rel1 = os.path.relpath(p1, root)

        if not p2:
            warnings.append(
                f"file2 not found for {info['file1']} (skeleton missing?); "
                "falling back to top-level chunks of file1 — confirm with user")
            raw_blocks = []
            i = 0
            while i < len(lines1):
                if lines1[i].strip():
                    end = default_extent_end(lines1, i)
                    raw_blocks.append((i, end))
                    i = end + 1
                else:
                    i += 1
        else:
            raw_blocks = diff_blocks(lines1, read_lines(p2))

        # Existing entries that resolve into this file.
        existing_for_file = []
        for k, entries in existing.get("files", {}).items():
            if os.path.normpath(k) == os.path.normpath(rel1) or \
               os.path.basename(k) == os.path.basename(rel1):
                for e in entries or []:
                    rng = resolve_entry_range(lines1, e)
                    if rng:
                        existing_for_file.append(rng)
                    else:
                        warnings.append(
                            f"existing entry anchor no longer resolves in {rel1}: "
                            f"{e.get('anchor')!r}")

        blocks = []
        doc_only_skipped = 0
        tq_states = triple_quote_states(lines1)
        for b in raw_blocks:
            trimmed = trim_block(lines1, *b)
            if not trimmed:
                continue
            start, end = trimmed
            if is_doc_only(lines1[start:end + 1], tq_states[start]):
                doc_only_skipped += 1
                continue
            anchor, extent, ambiguous = suggest_annotation(lines1, start, end)
            covered = any(not (es > end or ee < start)
                          for es, ee in existing_for_file)
            block = {
                "start_line": start + 1,
                "end_line": end + 1,
                "text": "\n".join(lines1[start:end + 1]),
                "anchor": anchor,
                "extent": extent,
                "ambiguous": ambiguous,
                "covered": covered,
            }
            blocks.append(block)

        result_pairs.append({
            "file1": rel1.replace(os.sep, "/"),
            "file2": info["file2"],
            "labs_md_line": info["labs_md_line"],
            "lab_context": info["lab_context"],
            "doc_only_skipped": doc_only_skipped,
            "blocks": blocks,
        })

    out = {
        "root": root,
        "pairs": result_pairs,
        "existing_entries": existing.get("files", {}),
        "warnings": warnings,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    total = sum(len(p["blocks"]) for p in result_pairs)
    uncovered = sum(1 for p in result_pairs for b in p["blocks"] if not b["covered"])
    doc_skipped = sum(p["doc_only_skipped"] for p in result_pairs)
    print(f"Pairs: {len(result_pairs)}  Blocks: {total}  "
          f"Uncovered (need notes): {uncovered}  "
          f"Doc-only skipped: {doc_skipped}  Warnings: {len(warnings)}")
    for w in warnings:
        print(f"  WARNING: {w}")


if __name__ == "__main__":
    main()
