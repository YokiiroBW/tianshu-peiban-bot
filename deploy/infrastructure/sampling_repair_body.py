"""Renderer input: repair metadata of two exact sampling mountpoint directories."""

import ast
import hashlib
import json
import os
from pathlib import Path


def main():
    load_original(readonly=False)
    require(os.geteuid() == 0, "sampling_repair_requires_host_root")
    receipt = S / (EXPECTED["receipt_prefix"] + "-sampling-repair.json")
    require(not receipt.exists(), "sampling_repair_receipt_exists")
    rows = snapshot.inspect_owners(D)
    require(len(rows) == 10 and {row["service"] for row in rows} == snapshot.OWNERS, "owner_set_changed")
    platform = next(row for row in rows if row["service"] == "platform")
    require(platform["id"] == EXPECTED["platform_id"] and platform["image_id"] == EXPECTED["image_id"]
            and platform["running"], "sampling_repair_platform_changed")
    path = host_path(rows, "platform", SAMPLE_STATE)
    require(str(path) == EXPECTED["sampling_underlay"] and not path.is_symlink()
            and path.is_dir() and not any(path.iterdir()), "sampling_repair_underlay_changed")
    raw = json.loads(command([D, "inspect", platform["id"]]).stdout)[0]
    require(raw["HostConfig"]["Memory"] == EXPECTED["memory_limit"] and SAMPLE_STATE in raw["HostConfig"].get("Tmpfs", {}), "sampling_repair_mount_changed")
    probe = r'''
import json,os
from pathlib import Path
p=Path('/var/lib/tianshu/infrastructure-sampling');s=p.stat();disk=os.statvfs(p)
assert os.geteuid()==os.getegid()==10001
assert not p.is_symlink() and p.is_dir() and p.is_mount()
assert s.st_uid==s.st_gid==10001 and disk.f_frsize*disk.f_blocks==128*1024*1024
REPAIR
s=p.stat();print(json.dumps({'uid':s.st_uid,'gid':s.st_gid,'mode':oct(s.st_mode&0o777),'is_mount':p.is_mount(),'bytes':disk.f_frsize*disk.f_blocks}))
'''
    arguments = [D, "exec", "--user", "10001:10001", "-i", platform["id"], "python", "-I", "-B", "-"]
    # Verify the exact live mount before changing either directory's metadata.
    before = json.loads(command(arguments, input=probe.replace("REPAIR", "pass"), timeout=15).stdout)
    prepare_sampling_mountpoint(path)
    info = path.stat()
    require(info.st_uid == info.st_gid == 10001 and info.st_mode & 0o777 == 0o700, "sampling_underlay_metadata_failed")
    after = json.loads(command(arguments, input=probe.replace("REPAIR", "p.chmod(0o700)"), timeout=15).stdout)
    require(after["mode"] == "0o700", "sampling_live_metadata_failed")
    save(receipt, {"status": "sampling_mountpoint_metadata_repaired", "platform_id": platform["id"],
                   "underlay": str(path), "underlay_empty": True, "uid": info.st_uid, "gid": info.st_gid,
                   "underlay_mode": oct(info.st_mode & 0o777), "live_before": before, "live_after": after,
                   "recursive": False, "files_changed": 0, "services_restarted": False,
                   "image_changed": False, "configuration_rewritten": False, "memory_limit": EXPECTED["memory_limit"]})
    print(receipt.read_text())


if __name__ == "__main__":
    main()
