# Task 6 Report: Mid-run remap + resume recovery

## Summary

Added model-route failure detection and automatic recovery in `RunManager._execute`. On model/tool/connection errors, the run probes the catalog, remaps the failed quick/deep role (up to 3 smoke-tested candidates), records `recovery_events`, pauses the parent, and auto-resumes via `resume_run`. One recovery chain per role per run; rate-limit pause behavior unchanged.

## Files changed

- `webapp/runs.py` — `is_model_route_error`, `role_for_agent`, `_attempt_model_recovery`, recovery branch in `_execute`, `recovery_events` / `recovery_roles_tried` init
- `tests/test_llm_recovery.py` — helper unit tests + mocked recovery integration test

## Tests run

```
pytest tests/test_llm_recovery.py tests/test_web_run_resume.py -v
```

9 passed (4 new recovery + 5 existing resume).

## Concerns / notes

- No root `AGENTS.md` in repo; followed task brief and user workflow rules.
- `is_model_route_error` treats any `"model"` + `"not found"` substring pair as recoverable (covers `model 'x' not found`).
- Recovery holds `_exec_lock` until `_execute` returns; child resume thread queues behind lock (same as normal run serialization).
- `webapp/runs.py` was untracked before this task; committed only `runs.py` + recovery tests.

## Git

- Branch: `feat/dynamic-model-picker`
- Starting SHA: `d2bcd0ee12748b7089591c21600ddcc6aa6b3d27`
- Ending SHA: `6f17c1732075cac3baa8950f06c3f014d1392945`
- Commit: `feat: remap LLM role and resume runs after model/tool failures`

## Fix: tighten `is_model_route_error` (false-positive regression)

**Problem:** Bare needles (`"404"`, `"does not exist"`, `"tool use"`) caused unrelated failures (ticker 404, missing resource, tool policy) to trigger model recovery.

**Change:** Require model/endpoint context for existence and 404 checks; keep specific phrases for tool-use endpoints and connection failures. Dropped bare substring matches.

**New regression tests:** `test_is_model_route_error_rejects_unrelated_failures` — asserts False for ticker 404, resource does not exist, tool use policy violation.

**Tests run (post-fix):**

```
pytest tests/test_llm_recovery.py tests/test_web_run_resume.py -v
10 passed in 11.40s
```

- Fix commit: `03cedcf0bfcc0c8961add9a86f479d0dce7169f9`
