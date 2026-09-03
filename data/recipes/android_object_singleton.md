---
id: android_object_singleton
triggers: [kotlin_object_seam]
---

# Kotlin `object` with Android / car seams

## MUST

- Call `Cut.method(...)` directly; mock static/object seams; teardown in `@After`.
- MockK lane: `mockkObject(Cut)` or `mockkStatic(Seam::class)`; `@After fun tearDown() { unmockkAll() }`.
- Reset listeners / StateFlow between tests.

## MUST NOT

- Invent constructor injection / Hilt / `@Inject` for Kotlin `object` singletons.
- Leave static listeners / StateFlow dirty across tests.

## Ordered write steps (1..N)

1. Prefer pure extracts when delta is math/mapping.
2. Arrange: `mockkObject` / `mockkStatic` + `@After unmockkAll`.
3. Stub hardware / Android fakes used by SOURCE.
4. Act: `Cut.method(...)`.
5. Assert + reset.

## Minimal sketch

```kotlin
class CutObjectTest {
  @After fun tearDown() {
    unmockkAll()
  }

  @Test fun method_delegatesToSeam() {
    mockkObject(Cut)
    every { Cut.method(any()) } returns expected
    assertEquals(expected, Cut.method(input))
  }
}
```

## Related api_doc

- Robolectric / MockK indexes for static mocks.
