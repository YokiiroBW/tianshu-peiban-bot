# R15 baseline with AL13 owner contract profiles

This bundle reconstructs the AL13 candidate contract trees from the coordination Git root plus a minimal R15 overlay. It does not change any protocol schema or consumer pin. The legacy root and frozen R15 contracts differ, so simply pointing all consumers at root/contracts is not equivalent.

- `cp`: Companion/Platform full tree, 276 files.
- `memory`: Memory narrow tree, 103 files. It retains its own Life/Common dependency graph.
- 100 file mappings are shared. Only 19 overlay files are stored (886,641 LF-normalized bytes): 17 shared historical differences in context-history/v1, memory-context/v1 and v2, source-sync-batch/v1; plus Memory's life-runtime/v2 manifest and schemas/life.json.
- The three AL13 packages (actor-self-memory/candidate-v1, knowledge-content/v1, life-read/v1) refer directly to current root files. Memory's knowledge-content/v1/manifest.json maps to root contracts/knowledge-content/v1/profiles/memory.manifest.json. There are no copied AL13 schemas in overlays.

## Rebuild

Requires only Python standard library. Keep materialize.py, manifest.json and overlays/ together. The bundle can be moved to a tracked directory unchanged; it contains no dependency on ignored runtime locations or absolute source paths.

```powershell
python materialize.py --root C:/path/to/coordination-repo --profile cp --output C:/new-empty/cp-contracts
python materialize.py --root C:/path/to/coordination-repo --profile memory --output C:/new-empty/memory-contracts
```

The output must be new or empty. All source files are hash-checked before writing; existing files are never overwritten. Output inventory and LF-normalized SHA-256 hashes are verified. Root file drift fails explicitly; it never changes unrelated pins or silently falls back to another owner profile. For a later release, review the changed sources and update the profile manifest as part of that release.

## Validation and limits

Both profiles were materialized into fresh temporary subdirectories and independently compared against the coordinator's AL13 owner test trees, then the generated check trees were removed. All runtime files match. One auxiliary file in each test tree, knowledge-content/v1/profiles/memory.manifest.json, was a stale nested copy; reconstruction intentionally uses the current root profile as requested. The actual Memory top-level Knowledge manifest matches exactly. See verification.json.

This candidate packaging profile records exact per-owner runtime inputs for AL13 validation. It does not replace deployed contracts or modify the legacy user-memory protocol. Rebuild both profiles before candidate packaging; publication of a new runtime remains a separate deployment step.
