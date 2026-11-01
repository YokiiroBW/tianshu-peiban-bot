# CR20 registered NAS helper restoration

The new native infrastructure owner accepted static configuration but the image
did not contain its registered SSH executor, relay, remote launcher or four
required schemas. Root's preserved NAS record also referenced removed runtime
paths. This patch installs a relocated deployment helper without importing any
old product/runtime/database.

`deploy/core/infrastructure/tianshu_infrastructure` reuses the frozen standalone
application `ops/infrastructure` collectors and fixed control protocol. Relative
imports remain within the new package. The detached remote worker now invokes
the new absolute `run_remote.py` with isolated Python, rather than an old module
name/PYTHONPATH. `run_remote.py --python` performs one explicit environment exec.
Only the necessary four schemas are included, verified against current native
client SHA256 constants. No fixture/example/history contract bundle is included.
Docker adds openssh-client; pyproject/uv.lock pin the existing helper's actual
PyYAML 6.0.3 requirement for bounded non-constructing Compose parsing.

Preserved: registered target-only OpenSSH with strict host-key checking, explicit
identity, disabled agent/proxy/forwarding, bounded process trees; same operation
ID, persistent sent-before-SSH journal, unknown readback instead of resubmit;
existing remote project/container/bind protections and completion leases.
Configuration and key material are never included in this commit. There is no
new business service, scheduler, SQL profile or compatibility import path.

Minimum verification: only `tests/deployment/test_infrastructure_helper.py`,
**4 passed in 0.79 s**, in an isolated uv overlay (root's existing Python, pinned
pytest/jsonschema/PyYAML). It exercises both real isolated catalog subprocesses
against the current schemas, remote environment/launcher relocation, original-ID
unknown recovery, pinned OpenSSH arguments and safe YAML handling. No SSH,
Docker, paid model, real target or full product suite ran. Lock resolution added
only PyYAML. Existing cancellation/process-tree source is reused.

Deployment paths and the native registration sequence are in the helper README.
Root's private `.runtime/config-migration-20261101` contains the generated
executor/relay/remote/settings/API plans and remaining external mapping. Root
reported the old NAS remote state directory absent and will not migrate or
resubmit old control operations. Actual original identity/known-hosts are kept;
read-only catalog/observe acceptance and image rebuild remain root's work.
