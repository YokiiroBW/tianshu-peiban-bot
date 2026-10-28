# Autonomous-life core owner profiles

This profile extends the exact R15/AL13 owner trees with actor-self-memory
candidate.4 and Companion/Platform LifeRuntime 2.1.0. It leaves the Memory owner's
narrow LifeRuntime dependency graph unchanged. Old user-memory protocols and
their pins are unchanged.

The manifest shares the already tracked historical overlays in
`../unified-r15-al13/overlays/`; it does not duplicate them or alter that old
profile's manifest. Use its standard-library materializer with the explicit new
manifest:

```powershell
python contracts/runtime-profiles/unified-r15-al13/materialize.py --root C:/repo --manifest contracts/runtime-profiles/unified-r15-al-core/manifest.json --profile cp --output C:/new/cp
python contracts/runtime-profiles/unified-r15-al13/materialize.py --root C:/repo --manifest contracts/runtime-profiles/unified-r15-al-core/manifest.json --profile memory --output C:/new/memory
```

All input hashes and output inventories are checked before any product test.
The output must be new or empty. Run with the Git root version corresponding to
the selected profile; later contract drift fails rather than changing old pins.
The initial core trees contain 278 CP files and 103 Memory files. This is an
isolated candidate input definition, not evidence of production deployment.
