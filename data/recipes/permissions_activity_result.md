---
id: permissions_activity_result
triggers: [android_fragment, android_activity, permissions]
---

# Permissions / ActivityResult / grant maps

## MUST

- Drive grant/deny via Robolectric shadow **instance**:
  `Shadows.shadowOf(context).grantPermissions(...)` /
  `Shadows.shadowOf(context).denyPermissions(...)`
  (`context` is usually `ApplicationProvider.getApplicationContext()`).
- Or mock `checkSelfPermission` on a test Context.
- Register ActivityResult callbacks before STARTED when testing launchers.

## MUST NOT

- `Shadows.grantPermissions(...)` as a static — **does not exist**.
- System permission dialog UI in JVM unit tests.

## Ordered write steps (1..N)

1. Arrange context + host Cut:

```kotlin
val context = ApplicationProvider.getApplicationContext<Context>()
Shadows.shadowOf(context).grantPermissions(Manifest.permission.ACCESS_FINE_LOCATION)
```

2. Register launchers before STARTED if needed.
3. Stub grant/deny for THIS arm via `shadowOf(context).grantPermissions` / `denyPermissions`.
4. Act: public permission path.
5. Assert branch outcome.

## Related api_doc

- `data/api_doc/robolectric_4_13_api_index.json`
