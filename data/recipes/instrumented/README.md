# Instrumented test recipes (`src/androidTest`)

Playbooks for the **instrumented coder pass** (on-device / connected tests + JaCoCo).
Unit/Robolectric recipes live in the parent [`../README.md`](../README.md) directory.

| Recipe | Lane | Notes |
|--------|------|-------|
| `instrumented_hilt_android_test.md` | Hilt + AndroidJUnit4 | Default ActivityScenario harness |
| `instrumented_hilt_fragment_test.md` | Hilt Fragment | FragmentScenario, nav host, `@BindValue` |
| `instrumented_work_manager_test.md` | WorkManager / Hilt Worker | `WorkManagerTestInitHelper`, TestDriver |
| `instrumented_foreground_service_test.md` | Foreground Service | Real FGS lifecycle on device |

Selection: `select_instrumented_recipe_files()` in `kotlin/catalog_context.py` (category tags from `test_layer.py`).

Frontmatter `triggers` are documentary; Python decision tree is authoritative.
