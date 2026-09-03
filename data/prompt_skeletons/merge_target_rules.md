## MERGE_TARGET_EXISTING
- If TARGET already exists: merge — keep every existing `@Test`, import, helper, runner, and class structure; add only new `@Test` methods (plus imports/helpers those new tests need). Do not overwrite, rewrite, rename, or delete prior tests.

## MERGE_TARGET_NEW
- If TARGET does not exist yet: create it once with Sections 2–5 infrastructure, then add this plan's `@Test` methods
