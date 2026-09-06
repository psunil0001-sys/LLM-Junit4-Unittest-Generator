## 3. Required Test Infrastructure
- Test runner: JUnit4 only (never MockitoExtension / @ExtendWith)
- Annotations: use existing target-file pattern
- JUnit4 rules: use existing target-file pattern
- Coroutine rule: runTest for suspend; MainDispatcherRule only if needed
- Robolectric config: use only when Android framework APIs are required
- Hilt setup: use only when the source/target already requires Hilt
- Application class: use existing target-file pattern
- Base test class: use existing target-file pattern
- Theme/resource requirements: use existing target-file pattern

## 4. Dependencies
| Dependency | Production Implementation | Test Strategy | Reason |
|---|---|---|---|
| source collaborators | production collaborators used by selected lines | fake/mock/real per existing target pattern | reach selected lines without side effects |

## 5. Hilt Strategy
- Uses Hilt: follow source/target pattern
- Hilt test application: use existing target-file pattern or N/A
- Activity @AndroidEntryPoint: full Hilt test setup BEFORE Robolectric.create() 
(@HiltAndroidTest + HiltAndroidRule + HiltTestApplication + BindValue); 
never create() then assign @Inject fields
- Modules to replace: only modules needed by selected lines
- Modules to uninstall: only modules needed by selected lines
- Test bindings: only bindings needed by selected lines
- Injected fields: only fields needed by selected lines

## 6. Android Lifecycle Strategy
- Required lifecycle states: use only states required to reach selected lines
- How to move between states: use standard test APIs already present in TARGET 
(siblings only for host APIs already proven there)
- Setup expected at each state: initialize only dependencies needed by selected lines
- Cleanup expected at each state: use existing target-file pattern

| Lifecycle State | Trigger | Expected Behavior | Test? |
|---|---|---|---|
| as required | public/test lifecycle entrypoint | selected lines execute | Yes |

## 7. Test Cases
| ID | Priority | Kover Lines | Tag | Branch Target | Scenario | Given | When | Then | Required Lifecycle State | Assertions |
|---|---|---|---|---|---|---|---|---|---|---|
