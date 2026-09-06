---
id: osmdroid_mapview
triggers: [osmdroid]
---

# osmdroid MapView unit tests

## MUST

- Prefer testing extracted map helpers (center, zoom, overlay add) with `GeoPoint` values — no live tiles.
- When SOURCE talks to `IMapController`, mock/mockk that interface; stub `setCenter` / `setZoom`.
- Robolectric Activity/Fragment host only if MapView inflates without network; otherwise `not_testable` with reason `osmdroid_mapview_jvm_host`.

## MUST NOT

- Download OpenStreetMap tiles or hit the network.
- Instrumentation `ActivityScenario` solely to show a map.
- Invent osmdroid APIs not in `data/api_doc/osmdroid_api_index.json`.

## Ordered write steps (1..N)

1. Imports: osmdroid types from SOURCE imports (Glob/Read) + MOCKING LANE.
2. Arrange: mock `IMapController` (and MapView only if construction is verified).
3. Stub controller members used on THIS branch.
4. Act: public CUT method that moves/zooms/overlays.
5. Assert: `verify(controller).setCenter(...)` / overlay list / returned GeoPoint.

## Related api_doc

- `data/api_doc/osmdroid_api_index.json`
