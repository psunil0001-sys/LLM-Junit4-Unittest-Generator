# Verified test recipes (planner playbooks)

Static Markdown playbooks for complex Android unit-test harnesses (`src/test`, Robolectric/JVM).
**Instrumented** androidTest playbooks live in [`instrumented/README.md`](instrumented/README.md).

Python picks **exactly one PRIMARY** harness via a decision tree (`select_recipe_lane` /
`selected_recipe_selection`), optionally **up to two COMPANION** stubs (CarUi + Navigation, etc.),
pins `recipe_id` + `recipe_lane` in plan frontmatter, and inlines Ordered write steps for
the primary only.

## Exclusive lanes (no mixups)

| Lane | When | Primary meaning |
|------|------|-----------------|
| `hilt_worker` | `@HiltWorker` | `hilt_worker_dowork` |
| `hilt_service` | EntryPoint Service | `hilt_service_full_harness` |
| `hilt_fragment` | EntryPoint Fragment (+ VM delegate → activity_viewmodels) | HiltAndroidRule + module / HiltTestActivity host |
| `hilt_activity` | EntryPoint Activity (not Fragment) | `hilt_android_activity_create` |
| `robolectric_non_hilt` | Plain Fragment/Activity UI | AppCompatActivity / FragmentScenario — **no** Hilt |
| `plain_jvm` | No UI attach | ViewModel / object / Apollo / Room / … |
| `instrumented_viewmodel` | Complex Hilt/Android-context/LiveData ViewModel | Android lifecycle host + Hilt/test doubles |

Companions (`carui_toolbar_host`, `android_navigation_host`, `permissions_activity_result`,
`osmdroid_mapview`, `flow_builder_cancellation_branch`) never redefine attach — catalog pastes
MUST stubs / technique only. Up to **two** companions may be selected (priority: Flow-cancel →
CarUi → Navigation → Permissions → osmdroid).

Private methods are never Act targets — cover via public entry points, or `not_testable`.

## Non-executable surfaces (do **not** generate tests)

Pure Kotlin **interfaces**, callback interfaces, and Hilt `@Module` / `@Binds`-only interfaces
(`TripsRepo`, `CreateTripRepo`, `JLRepoModule`, `LocationListenerJL`, …) have **no executable
bytecode**. Kover omits them; JaCoCo may still list the class under androidTest with null % —
that is a report artifact, not an open gap.

UnitTest_gen exits with `no_open_gaps` / dashboard chip **`no-exec`**. Cover logic via `*Impl`
/ `Remote*DataSource` / consumer tests instead. Do not invent interface-only unit or
instrumented tests.

Modules without a main Hilt Activity may ship test-only `HiltTestActivity` under
`src/test` for **Hilt** Fragments only (never for `robolectric_non_hilt`).

## Generic CUT (all recipes)

- Name the class under test `Cut` in sketches (SOURCE simple name is fine in approach).
- One mocking lane (mockito **or** mockk) as pinned by the plan.
- Recipes give harness **order**. Library signatures live in `data/api_doc`.

| File | Typical triggers |
|---|---|
| `hilt_android_activity_create.md` | `hilt_android_activity` |
| `hilt_fragment_lifecycle.md` | EntryPoint Fragment, no VM delegate |
| `hilt_fragment_activity_viewmodels.md` | EntryPoint Fragment + `viewModels` / `activityViewModels` |
| `plain_fragment_activity_viewmodels.md` | plain Fragment + `viewModels` / `activityViewModels` |
| `plain_activity_robolectric.md` | non-Hilt Activity |
| `hilt_worker_dowork.md` | `hilt_worker` |
| `hilt_service_full_harness.md` | `hilt_service` |
| `room_in_memory_dao.md` | `@Dao` / Room |
| `osmdroid_mapview.md` | osmdroid (often companion) |
| `carui_toolbar_host.md` | CarUi (companion) |
| `android_navigation_host.md` | navigation (companion) |
| `permissions_activity_result.md` | permissions (companion) |
| `viewmodel_flow_livedata.md` | ViewModel SOURCE file |
| `complex_viewmodel_android.md` | Hilt/Android-context/LiveData-heavy ViewModel |
| `android_object_singleton.md` | `kotlin_object_seam` |
| `worker_service_receiver.md` | `worker_service` |
| `apollo_client_network_transport.md` | Apollo |
| `flow_builder_cancellation_branch.md` | companion: `flow { emit }` partial-branch cancel/exception |

Frontmatter `triggers` columns are **documentary**; the Python decision tree in
`kotlin/recipes.py::_select_primary_recipe()` is authoritative.

Prefer these playbooks; mark `not_testable` only when the JVM host still cannot
construct the view/DB or no public entry reaches the private gap.

## Harness health (post-fix reassessment)

Grades: **A** = aligned with proven tests, no contradictions, fix pass retains key sections;
**A-** = strong with minor edge cases; **B+** = usable, narrower scope.

| Recipe | Role | Grade | Notes |
|--------|------|-------|-------|
| `apollo_client_network_transport.md` | primary / JVM | A | QueueNetworkTransport + apolloHttp; fix pass includes Case table |
| `hilt_fragment_lifecycle.md` | primary | A | Theme before setup; ShadowLooper; HiltTestActivity |
| `hilt_android_activity_create.md` | primary | A | Theme before create; CarUi static stub |
| `hilt_service_full_harness.md` | primary | A | startService + buildService fallback; @UninstallModules |
| `hilt_worker_dowork.md` | primary | A | AssistedInject + mock WorkerParameters |
| `hilt_fragment_activity_viewmodels.md` | primary | A- | Fixed bootstrap order; VM seed before commitNow |
| `plain_fragment_activity_viewmodels.md` | primary | A- | AppCompat theme; plain host bootstrap |
| `plain_activity_robolectric.md` | primary | A- | Theme before setup (was contradictory; fixed) |
| `carui_toolbar_host.md` | companion | A- | OEM stub copy-from-module guidance |
| `android_navigation_host.md` | companion | A- | Post-attach setViewNavController timing |
| `viewmodel_flow_livedata.md` | primary / JVM | B+ | HiltViewModel + MainDispatcherRule added |
| `room_in_memory_dao.md` | primary / JVM | B+ | In-memory + destructive migration note |
| `permissions_activity_result.md` | companion | B+ | Shadow grantPermissions example; tag trigger |
| `osmdroid_mapview.md` | companion | B+ | IMapController mock; not_testable escape |
| `flow_builder_cancellation_branch.md` | companion | A | Proven on TripsRepoImpl: cancel+exception → ~75% branch |
| `worker_service_receiver.md` | primary | B+ | Redirects Hilt Service to full harness |
| `android_object_singleton.md` | primary / JVM | B+ | mockkObject sketch; still intentionally minimal |

No recipe remains at **C** (thin or contradictory). Remaining **B+** items are narrow by design (companions / object seams) rather than missing critical guidance.
