Write this Markdown document to PLAN OUTPUT FILE (YAML frontmatter + body below).
Chat answer is a short ack only (`PLAN_SAVED`). Do not paste this document into chat.

Frontmatter MUST look like:

---
schema_version: 2
source_file: <absolute SOURCE path>
test_file: <absolute PIPELINE TARGET *Test.kt path>
mocking_lane: mockito   # pinned by resolve_mocking_lane; open TARGET → mockito
recipe_id: none         # PRIMARY stem from SELECTED RECIPE (pipeline-owned)
recipe_lane: plain_jvm  # pipeline-owned lane — must match SELECTED LANE
primary_test_layer: unit  # assigned by pipeline after clamp (unit | instrumented)
delta_allow_list: [<only DELTA line numbers from the allow-list>]
testable:
  - lines: [<delta lines for THIS scenario only>]
    tag: <one COVERAGE TAG>
    test_layer: unit  # pipeline assigns after clamp — do not set in plan agent Write
    methods: [<public entry points only — never private method names as Act>]
    tc_ids: [TC01]
    branch_targets: ["<missing &&/|| arm if partial branch; else empty>"]
    approach: |
      1. Arrange/stub per PRIMARY recipe (do not re-paste full runner/Hilt — cite recipe_id).
      2. Stub/mock only THIS scenario's seams (MOCKING LANE syntax).
      3. Act: the single public call / lifecycle step that hits the listed Kover lines.
      4. Assert: observable result that proves those lines.
    fixture_hint: "<stubs / Robolectric / fakes for THIS scenario — same mocking_lane syntax only>"
    risks: []
  - lines: [<delta lines for THIS scenario only>]
    tag: <one COVERAGE TAG>
    methods: [<same or other public entry points>]
    tc_ids: [TC02]
    branch_targets: []
    approach: |
      1. Arrange/stub per PRIMARY recipe.
      2. Stub/mock only THIS scenario's seams.
      3. Act: the single public call for THIS item's lines.
      4. Assert: observable result for THIS scenario only.
    fixture_hint: "<stubs scoped to THIS scenario only>"
    risks: []
not_testable:
  - lines: [<delta lines deferred>]
    tag: <one COVERAGE TAG>
    methods: []
    reason: "<why not unit-testable>"
    category: <e.g. private_no_public_entry | needs_carui_toolbar_seam | kover_residual_branch | not_unit_testable>
---

Body MUST use these sections (keep headings exactly):

# Test Plan

## 1. Target
- Class under test: ...
- CUT construction: <cite recipe_id / recipe_lane — one line>
- Method/feature under test: ...
- Out of scope: ...

## 2. Infra deltas
- Only differences vs the PRIMARY recipe (runner/host/theme/rules). Else write: `per recipe`
- Test runner: <JUnit4 only — never MockitoExtension / @ExtendWith>
- Host: <AppCompatActivity | HiltTestActivity | N/A — must match recipe_lane>

## 3. Test Cases
| ID | Kover Lines | Tag | Branch Target | Assertions |
|---|---|---|---|---|
| TC01 | 37,38 | android_fragment | permission denied short-circuit | ... |

Every testable delta line MUST appear in at least one row (Kover Lines).
Partial-branch rows MUST name the missing truth combination in Branch Target.
One frontmatter testable item = one scenario = one TC row (tc_ids: exactly one id).
Assertions MUST use frontmatter mocking_lane syntax only.

## 4. Deferred
| Kover Lines | Tag | Category | Reason |
|---|---|---|---|
| 89 | carui_toolbar | needs_carui_toolbar_seam | ... |

Every not_testable delta line MUST appear here. Empty table only when not_testable is [].
Put each DELTA line in exactly one of: testable (Section 3) or not_testable (Section 4).
