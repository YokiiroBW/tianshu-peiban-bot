# Infrastructure executor source package

This package supplies the TS-176 single-request JSON executor to a configured
platform process. It is a source ZIP, not a new service, node installation or
production deployment. The build tool uses only the Python standard library
and Git. Runtime needs Python 3.11+, the pinned PyYAML 6.0.3 parser and an
explicit OpenSSH client on the machine/container that launches the executor.
Remote Linux/Synology/OpenWrt nodes do not need Python.

## Build from an immutable input

Run the builder from this repository using the coordinator's **full 40-character
TS-176 implementation SHA**, not `HEAD`, a branch or a source directory copy:

```text
python -B -m deploy.infrastructure build --repo /absolute/source-repository --commit FULL_40_CHARACTER_COMMIT --output /absolute/artifacts/infrastructure-node.zip
python -B -m deploy.infrastructure verify /absolute/artifacts/infrastructure-node.zip
```

The output file must be new. The builder does not change the source repository,
install dependencies, contact nodes, build an image or start services. A missing
declared source file, protocol/schema mismatch or dependency-lock change is an
explicit packaging refusal. `bundle.py` owns the fixed source and asset lists.
It reads literal Git blobs with `ls-tree` / `cat-file`; a dirty index/worktree,
untracked config, and Git `export-ignore` / `export-subst` attributes cannot
alter the selected input. Candidate contract files come from that **same SHA**.

Each ZIP entry has a fixed 1980 timestamp, regular-file mode 0644, sorted name
and no compression. Package-owned text assets use canonical LF newlines.
Repeating a build with the same executor SHA and the same
packager code/assets produces the same bytes. `package-manifest.json` records
source commit, protocol, contract digest, runtime dependency versions and each
payload file's SHA256/length/origin. The CLI returns the whole ZIP's SHA256.
The manifest excludes its own digest to avoid a circular hash. Verification
checks the declared names, file hashes, versions and contract digest; it does
not fetch remote code or establish a release signature.

Only the declared `ops/infrastructure` code/README/requirements, the candidate
README and three schemas, its purely synthetic config example, and these
package-owned runtime/deployment assets are included. Real config, identity
files, known_hosts, sampling state, `.env`, tests/SSH fixtures and all other
products are excluded even if tracked beside the selected source. The package
adds an empty `ops` namespace marker so imports cannot resolve to a different
installed package. No private input directory is accepted by the builder.

## Run after unpacking

Verify the archive, unpack it into a dedicated code directory, and select a
separate Python environment. The archive does not carry a machine-specific
venv or compiled wheel. Install its fixed, hashed release artifacts:

```text
/absolute/python -m pip install --only-binary=:all: --no-deps --require-hashes -r /absolute/node-package/requirements.lock
/absolute/python -I -B /absolute/node-package/run_node.py --version
/absolute/python -I -B /absolute/node-package/run_node.py --config /absolute/private/executor.json
```

The lock includes the published PyYAML 6.0.3 release artifact hashes for the
supported wheel platforms. `--only-binary` avoids an unpinned build toolchain;
an unsupported Python/OS must supply an explicitly reviewed compatible wheel.
The selected interpreter must have exactly the locked parser version. A missing
or different version exits 2 with `infrastructure_dependency_unavailable` and
the exact requirement on stderr. `--version` works before dependency installation.

The working directory can be an unrelated empty directory. `run_node.py` adds
only its own unpacked code root when used with `-I`; no author checkout,
PYTHONPATH or virtualenv path is embedded. Send one UTF-8 request, then close
stdin. Stdout contains the executor's one JSON response. Domain failures follow
the candidate protocol and exit 0; package/dependency/CLI failures exit 2.
For a sanitized Windows caller environment, retain the non-secret system
`PROGRAMDATA` variable alongside PATH/SYSTEMROOT/WINDIR/TEMP/TMP/LANG/LC_ALL;
the installed Windows OpenSSH client otherwise exits 255 before producing
output. No user profile, SSH agent or other credential environment is needed.

For an empty catalog, copy `examples/executor-empty.json` **outside the package**,
set an explicit absolute writable local `state_dir` and explicit SSH command,
and leave `nodes` empty. Send:

```json
{"version":"infrastructure-node.v1","request_id":"package:catalog","node_id":null,"operation":"node.catalog","params":{},"timeout_ms":1000}
```

No SSH is contacted by an empty catalog. Register real nodes only in the
deployment-owned config. The original contract's `examples/config.example.json`
uses an `.invalid` host and synthetic paths; it is not a usable real connection.
Identity and known-host files must remain outside the source package.

## Platform image and configuration review inputs

The current accepted platform image has ffmpeg but lacks OpenSSH and this node
package. `deployment/Dockerfile.example` is a **derived-image review input** for
that existing Debian/Python image. It installs OpenSSH, copies this verified
package root from `./node-package/`, and installs the parser into its own
`/opt/tianshu-infrastructure-venv`. It inherits the existing platform
ENTRYPOINT/CMD and does not alter its application venv/PATH. Use the accepted
platform image digest as `PLATFORM_IMAGE`; record the OpenSSH package/version,
resolved image digest and package SHA in the eventual deployment plan. Only
the source ZIP is claimed byte-reproducible; an apt/OCI build still requires
its own resolved repository/image inputs and Linux validation.

`deployment/platform-settings.fragment.json` matches TS-177's server-side
executor command/directory and contract directory. Merge it with the existing
settings; `infrastructure.directory` must equal the existing initialized
`web_external.directory`, not create a second credential/catalog store. The
example path is illustrative. The explicit executor argv is:

```text
/opt/tianshu-infrastructure-venv/bin/python -I /opt/tianshu-infrastructure-node/run_node.py --config /etc/tianshu/infrastructure/executor.json
```

The code is image-owned, root-owned, mode 0644 under mode-0755 directories and
read-only to platform UID/GID 10001. `deployment/compose.fragment.json` is an
unapplied merge input for the existing `platform` service, with an explicit
derived image digest, a read-only config file and a read-only directory of SSH
identity/known-host references. It adds **no Docker socket mount** and no new
service. Ensure directory traversal/read access for UID 10001; private OpenSSH
identity files must also satisfy OpenSSH's ownership/mode rules for that UID.
Do not mount the user's whole SSH home or copy credentials into an image.

The example gives sampling state its own **128 MiB bounded local tmpfs**, UID/GID
10001 mode 0700, shared by executor invocations. This state is only counter
baselines and locks; container recreation honestly produces first samples.
It is not full logs or historical metrics. The bound includes retired-node
files, which the executor does not automatically prune; filling it produces
explicit state failures. A persistent alternative needs an equally explicit
local capacity bound and retirement policy in the deployment plan. Do not use
SMB/NFS for cross-process locks. The tmpfs/memory reservation and correct mode
still need actual Compose 2.20/DSM readback before deployment acceptance.

For the protected resident stack, the new image, extra config/credential binds,
and state/memory budget are inputs to its existing controlled deployment and
capacity-guard generation. Match every bind source/target/RW bit to that plan;
do not overlay this fragment on a live stack or loosen guard mount/identity
checks. Code packaging does not authorize stopping/recreating any real service.

## Package acceptance

`tests/deployment/infrastructure/test_package.py` verifies immutable Git input,
repeatability, exclusion, version/lock failures, archive integrity and a real
outside-directory launch using declared synthetic Git code. That unit fixture
does not substitute for the actual TS-176 executor.

The final package acceptance exports the coordinator's fixed TS-176 code plus
its fixed **test-only** synthetic SSH helper into separate temporary locations,
launches the packaged executor with isolated Python from an unrelated directory,
and checks empty catalog, node identity, host/OpenWrt observations, all Docker
pages above 64 containers, metrics, bounded logs and Compose projection. The
fixture is not included in the source ZIP. It uses only loopback and ephemeral
synthetic keys. Exact command, source SHA, results and unavailable real Docker /
NAS / OpenWrt checks belong in the TS-178 handoff.
