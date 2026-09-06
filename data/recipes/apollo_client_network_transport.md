---
id: apollo_client_network_transport
triggers: [apollo]
---

# Apollo Client network transport (JVM) — Apollo Kotlin 3.8.2

## MUST

- Target **Apollo Kotlin 3.8.2** (`com.apollographql.apollo3:apollo-runtime:3.8.2`) with
  `testImplementation("com.apollographql.apollo3:apollo-testing-support:3.8.2")` for official test transports.
- Build `ApolloClient` with `ApolloClient.Builder().networkTransport(QueueTestNetworkTransport()).build()`
  (or `MapTestNetworkTransport()` when mapping Operation → response).
- Enqueue with `client.enqueueTestResponse(operation, data, errors)` /
  `client.enqueueTestNetworkError()` (Queue) or `registerTestResponse` / `registerTestNetworkError` (Map).
- Generated Operation / Data types come from SOURCE imports (default package placeholder **`your.app.api`**,
  or `TESTGEN_APOLLO_API_PACKAGE` when configured). Never invent Mutation/Query classes.
- Suspend `execute()` paths: wrap Act in `runTest { }`.
- If the module already ships harness helpers (`mockApollo`, `QueueNetworkTransport.enqueueThrow/enqueueResponse`,
  `apolloHttp(statusCode)`), you **may** use those instead of inventing parallel wrappers — still prefer official
  Apollo 3 testing APIs when both are available.

## MUST NOT

- Real network / production `HttpNetworkTransport` against live endpoints in unit tests.
- `whenever(client.query(any())).thenThrow(...)` / Mockito `thenThrow` on `query`/`mutation`.
- Construct raw `ApolloHttpException(...)` with guessed ctor args.
- `Read` / import from `build/generated`; invent generated ctor args by browsing `build/`.
- Use Apollo **4/5** coordinates (`com.apollographql.apollo` without `apollo3`) unless SOURCE already does.

## Ordered write steps (1..N)

1. Imports: `ApolloClient`, `QueueTestNetworkTransport` (or Map), enqueue helpers, SOURCE operations under `your.app.api`.
2. Arrange: `ApolloClient.Builder().networkTransport(QueueTestNetworkTransport()).build()`.
3. Stub/enqueue: `enqueueTestResponse(op, data)` or `enqueueTestNetworkError()` for the Case under test.
4. Act: repository/use-case that calls `client.query(op).execute()` (inside `runTest` if suspend).
5. Assert: mapped domain result / errors / exception / collaborator interaction — not transport internals.
6. Teardown: `client.close()` or `client.dispose()` in `@After` when the client is test-owned.

## Minimal sketch

```kotlin
val client = ApolloClient.Builder()
    .networkTransport(QueueTestNetworkTransport())
    .build()
val op = ExampleQuery() // from your.app.api — use the real generated type from SOURCE
client.enqueueTestResponse(op, /* data = */ ExampleQuery.Data(/*...*/))
runTest {
    val response = client.query(op).execute()
    assertEquals(expected, response.dataAssertNoErrors /* or map through CUT */)
}
```

HTTP / network failure sketch:

```kotlin
client.enqueueTestNetworkError()
runTest {
    assertFailsWith<ApolloNetworkException> {
        client.query(op).execute()
    }
    // or assert response.exception when the CUT surfaces ApolloResponse
}
```

Optional project harness (only if present in-module):

```kotlin
val transport = QueueNetworkTransport()
transport.enqueueThrow(apolloHttp(401))
val client = apolloClient(transport)
```

## Related api_doc

- `data/api_doc/apollo_3_8_2_api_index.json`
