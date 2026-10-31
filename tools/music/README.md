# Native music tools

These two existing one-request tools are built into the single-product image.
They use JSON over stdin/stdout and do not own a TianShu account store, listener,
task queue or database. The capabilities media domain continues to own routing,
credentials, source permissions, cancellation and receipts.

- `musicbridge/`: QQ Music and Kugou adapter, pinned `music-lib`
  `v1.1.1-0.20261013052135-deb29cf7c490`; AGPL-3.0-or-later, with its original
  [license](musicbridge/LICENSE).
- `musicdecode/`: offline NCM/QMC/KGM decoder; pinned Unlock Music CLI v0.2.12
  with the original vendored [license](musicdecode/third_party/unlock_cli/LICENSE),
  hashes and modification record in
  [UPSTREAM.json](musicdecode/third_party/unlock_cli/UPSTREAM.json).

`build.py` preserves the existing pinned Go 1.27.2 archive and SHA-256, verifies
the modules against their checked-in `go.sum`, builds both tools with
`CGO_ENABLED=0` and `-mod=readonly`, vendors their locked dependencies, and bundles
the corresponding source, build script and licenses. `deploy/core/Dockerfile`
runs that independent build stage and installs:

```
/usr/local/bin/tianshu-musicbridge
/usr/local/bin/tianshu-musicdecode
/usr/share/tianshu-music-tools/corresponding-source.tar.gz
```

The existing media domain discovers these binaries through `PATH`; no additional
service URL or executable environment variable is required. User accounts and
entitlements remain necessary for account-specific operations. The decoder
remains offline and does not invent missing MMKV/client-vault keys; unsupported
or key-dependent formats retain their original explicit errors.

Source-only migration: both tool trees came from the declared, frozen Platform
source snapshot, without changing the Go algorithms, dependency locks or protocol.
There is no runtime dependency on that snapshot or the retired Platform package.
