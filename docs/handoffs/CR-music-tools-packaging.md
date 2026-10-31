# CR19 native music tools packaging

Status: `implemented_not_tested`. No build, tests, SSH, external service, account
login or provider call was executed by this implementer.

The integrated image omitted the two already implemented local music binaries.
`Music.capabilities()` consequently marked QQ/Kugou unavailable, and encrypted
audio acquisition/import could not find the decoder. This was a packaging gap,
separate from the expected requirement to sign in with the user's own account.

## Change and ownership

Migrated frozen `platform/tools/musicbridge/**` and `musicdecode/**`, including
the vendored Unlock Music adaptation and original licenses, into `tools/music/`.
The existing fixed Go 1.27.2/SHA-256 build script now resides at
`tools/music/build.py`. Go source, module versions and `go.sum` are preserved.
The build still verifies modules, produces static binaries, vendors locked
dependencies and packages corresponding source and license material.

The independent Docker build stage installs both binaries into `/usr/local/bin`
and corresponding source into `/usr/share/tianshu-music-tools/`. Its inputs are
separate from the frequently changing Python/web product sources; installation
follows the expensive source-independent runtime dependency/browser layers.
`.dockerignore` permits only `tools/music`, and the public release build's Git
archive includes that same path. No parallel music service, account ledger or
legacy Platform runtime import was added.

Docker/ignore/release-script baseline was the parent's integrated `ca7e991`.
Local-only baseline commit `1f9c116` is not an integration delivery; cherry-pick
only the subsequent actual repair commit. Root's separate private NAS archive
script must likewise include `tools/music` (root owns that file).

## Minimal acceptance commands for the coordinator

After the coordinator builds the image, execute only these local version requests
first; neither operation contacts a music provider or needs an account:

```sh
printf '%s\n' '{"operation":"version"}' | /usr/local/bin/tianshu-musicbridge
printf '%s\n' '{"operation":"version"}' | /usr/local/bin/tianshu-musicdecode
tar -tzf /usr/share/tianshu-music-tools/corresponding-source.tar.gz
```

Bridge result must be `ok:true` and include music-lib revision
`deb29cf7c49051df9744d788e2a573b280b300f2`; decoder result must be `ok:true` and
identify Unlock Music `v0.2.12`. The source tar must include both `go.mod`/`go.sum`,
the bridge license, the Unlock Music license/UPSTREAM record, build script, and
the build-generated dependency vendor trees. These checks do not establish real
user login, entitled downloads or compatibility with the user's encrypted files.

The existing frozen tool tests are retained alongside their Go source for the
coordinator's necessary focused verification. No new test framework was added.
The original decoder's missing-key/unsupported-format boundaries are unchanged.

## Configuration handoff

The independent new-install configuration proposal was delivered to A at private
`.runtime/new-install-config-20261101/{settings.merge,inputs,publisher,domain-settings}.json`.
A owns the actual settings/bootstrap changes; this repair does not modify those
files or manufacture Books/Music root-settings fields or external service IDs.
