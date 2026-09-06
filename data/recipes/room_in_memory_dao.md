---
id: room_in_memory_dao
triggers: [room]
---

# Room in-memory DAO unit tests

## MUST

- CUT is the `@Dao` (or queries on the `@Database`). Host with
  `Room.inMemoryDatabaseBuilder(context, CutDb::class.java).allowMainThreadQueries().build()`.
- Context: `ApplicationProvider.getApplicationContext()`.
- `@RunWith(RobolectricTestRunner::class)`.
- `@After` `db.close()` (use `try/finally` in tests if needed).
- Entity types and query methods come from SOURCE; do not invent columns.
- Suspend DAO methods: wrap Act in `runTest { }`.
- Schema mismatch in tests only: `.fallbackToDestructiveMigration()` on the in-memory builder — **never** on production DB builders.

## MUST NOT

- `Room.databaseBuilder` / on-disk DB / `createFromAsset` in unit tests.
- Mock the DAO when the DAO is the class under test.
- Real device Room / instrumentation `MigrationTestHelper` unless that is the only verified seam (then `not_testable`).

## Ordered write steps (1..N)

1. Imports: Room testing, Robolectric, ApplicationProvider, SOURCE entities/DAO/DB.
2. Arrange: in-memory DB + dao; insert seed rows for THIS query.
3. Stub: none unless the DAO calls a collaborator outside Room.
4. Act: the DAO method under test (`runTest` if suspend).
5. Assert: returned entities / counts / Flow first value.
6. Teardown: `db.close()` in `@After`.

## Related api_doc

- `data/api_doc/room_testing_api_index.json`
