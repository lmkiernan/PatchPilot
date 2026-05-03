# Revision Prompt Design

Used on retry attempts (attempt 2+) when the first patch did not fix the failure.

## Additional context sent to the LLM

```json
{
  "previous_attempt": {
    "patch": "--- a/src/pricing/calc.py\n+++ ...",
    "result": "failed",
    "new_failure": {
      "error_type": "AssertionError",
      "message": "assert 100 == 100 * 1",
      "raw_traceback": "..."
    }
  }
}
```

## System prompt addition for revision

Appended to the base system prompt:

```
A previous repair attempt was made but did not fix the failure.
The patch that was tried and the resulting failure are included below.
Do not repeat the same fix. Diagnose the new failure and produce a different patch.
```

## Key difference from initial prompt

The revision prompt includes `previous_attempt` so the model can reason about
why its first fix failed, rather than generating the same patch again.

The `previous_attempt.result` will always be `"failed"` in the revision case.
If a patch partially fixed things (e.g. targeted test passes but full suite
fails), that distinction is communicated in `new_failure`.
