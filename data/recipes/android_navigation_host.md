---
id: android_navigation_host
triggers: [android_navigation]
---

# Navigation / NavController — host + destination asserts

## MUST

- `TestNavHostController(ApplicationProvider.getApplicationContext())`.
- `navController.setGraph(R.navigation.<this_module_graph>)` then
  `Navigation.setViewNavController(view, navController)`.
- **Hilt Fragment attach:** after `commitNow()`, install on the fragment view:

```kotlin
activity.supportFragmentManager.beginTransaction()
    .add(android.R.id.content, Cut(), "cut")
    .commitNow()
ShadowLooper.idleMainLooper()
Navigation.setViewNavController(fragment.requireView(), navController)
```

- **Full ActivityController lifecycle:** install nav after `start()` and **before** `resume()` so `onResume` does not fire before the controller is wired.
- `setCurrentDestination` only with ids/routes from **this module's** nav XML.
- Assert `navController.currentDestination?.id` / args.

## MUST NOT

- Invent `navigate` overloads or destination ids not in module resources.
- `resume()` then `setViewNavController` when SOURCE navigates in `onResume`.

## Ordered write steps (1..N)

1. Imports: `androidx.navigation.testing.TestNavHostController`, `Navigation`, MOCKING LANE.
2. Arrange host Cut + `TestNavHostController`; `setGraph` from this module.
3. Stub gates (auth/repo from SOURCE) only.
4. Act: attach fragment / public navigate trigger; wire nav controller at correct lifecycle point.
5. Assert destination / args from this module's graph.

## Minimal sketch

```kotlin
val navController = TestNavHostController(ApplicationProvider.getApplicationContext())
navController.setGraph(R.navigation.cut_graph)
// after fragment attach + idleMainLooper:
Navigation.setViewNavController(fragment.requireView(), navController)
// act public navigate on Cut
assertEquals(R.id.cut_dest, navController.currentDestination?.id)
```

## Related api_doc

- `data/api_doc/navigation_testing_2_8_api_index.json`
