# Repair Prompt Design

## System prompt intent

The system prompt enforces three things:

1. **Output format**: git-style unified diff only, starting with `diff --git` on line one.
   No markdown, no explanation. The LLM is told this explicitly and the
   format is shown literally so there is no ambiguity.
   `_extract_diff` first looks for `diff --git`, then falls back to a plain
   `--- / +++` header in case an older model omits the git prefix.

2. **Scope constraints**: derived from the repair packet's `constraints` block
   at runtime — max files, allowed files, test edit policy.
   When `allow_test_edits=False` the rule is absolute: "Do not modify test files."
   The phrase "unless the test assertion is clearly wrong" is intentionally absent —
   it is the kind of rationalization the LLM will exploit.

3. **Quality preferences**: minimal change, preserve APIs, no cleanup.
   These are stated as rules rather than suggestions.

## User message intent

The user message sends only the fields the model needs to generate the diff:

- `error` — what failed and why
- `target` — file, line, function, expression
- `source_context` — the enclosing function's full source
- `imports` — top-level imports for namespace context
- `tests` — the failing test bodies as verification criteria
- `constraints` — rules the patch must follow

`confidence` and `verification` are stripped — they are pipeline metadata,
not inputs to the repair decision.

## Extraction

`_extract_diff` handles:
- Clean diff output (ideal)
- Diff wrapped in ```diff ... ``` (common)
- Prose before the `--- a/` header (trimmed)

The `---` / `+++` pair is the unambiguous start of a unified diff header.
Anything before it is discarded.

## Revision prompt

See `revise_prompt.md` for the retry variant, which includes the previous
patch attempt and the new failure output.
