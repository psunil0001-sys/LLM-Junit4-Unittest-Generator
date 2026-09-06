EXECUTE BLUEPRINT ONLY
Implement the blueprint test cases for THIS SLICE only.
Write from plan + SOURCE + TARGET.
Do not replan, defer, or re-classify coverage gaps.
Do not re-analyze coverage beyond the line numbers listed in the plan TC table.
Partial branches: implement the named Branch Target exactly — not another happy path.
When Branch Target / recipe is ``flow_builder_cancellation_branch`` (or DELTA is mb>0 on
``flow {{ emit(dep...) }}`` and TARGET only has ``.first()``): add ``launchIn`` +
``cancelAndJoin`` with delaying upstream and/or slow ``onEach`` collector, plus a
``throws`` + ``first()`` exception arm — see that companion recipe. Do NOT re-fight gaps
already marked ``kover_residual_branch`` after cancel/exception probes exist.
{source_read} SOURCE/TARGET only to fix compile errors, not for coverage triage.
