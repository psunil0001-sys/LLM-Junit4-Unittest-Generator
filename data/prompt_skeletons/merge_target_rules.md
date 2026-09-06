## MERGE_TARGET_EXISTING
- If TARGET already exists: merge — keep every existing `@Test`, import, helper, runner, and class structure; add only new `@Test` methods (plus imports/helpers those new tests need). Do not overwrite, rewrite, rename, or delete prior tests. Missing hosts, AndroidManifest.xml, nav/layout XML, or gradle test deps may still be Written under src/test or src/androidTest.

## MERGE_TARGET_NEW
- If TARGET does not exist yet: Write a complete JUnit4 class (package, imports, class = file stem, annotations/rules, `@Test` for this slice) — not an empty skeleton. Also Write any missing harness under the owning module test roots.
