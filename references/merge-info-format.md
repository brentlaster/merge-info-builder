# merge-info.json format (Merge Info extension)

Placed at the lab repo root. The extension loads it, resolves each annotation
to a line range in the named file, shows a gutter info icon on the range's
first line, and pops a markdown hover over the whole range — including in the
left pane of `code -d <complete> <skeleton>` diff views.

```json
{
  "files": {
    "extra/target_agent_complete.py": [
      {
        "anchor": "HIJACK_PATTERNS = [",
        "endAnchor": "]",
        "title": "Hijack detection patterns",
        "note": "Markdown **description** shown in the hover popup."
      },
      {
        "anchor": "RESTRICTED_INTENT = [",
        "lines": 2,
        "title": "Restricted intents",
        "note": ["Notes can also be an array of strings,", "joined with newlines."]
      }
    ]
  }
}
```

## Resolution rules (must match exactly)

- Keys under `files` are repo-relative paths to the annotated (complete)
  file. If the open document's path doesn't match a key exactly, the
  extension falls back to matching by **basename** — so keys must have
  unique basenames across the repo.
- `anchor` (required): the block starts at the FIRST line in the file whose
  text *contains* this string. If the anchor text also appears earlier in the
  file, the popup attaches to that earlier line instead — anchors must
  first-match at the intended block start.
- Block end, priority order:
  1. `lines`: N — block is exactly N lines (anchor line included).
  2. `endAnchor`: text — block ends at the first line AFTER the anchor line
     containing this text.
  3. neither — block runs until the line before the next blank line (or EOF).
- `title`: shown bold with a lightbulb icon at the top of the popup.
- `note`: markdown string, or array of strings joined with newlines (useful
  for multi-paragraph notes since JSON strings can't contain raw newlines).
