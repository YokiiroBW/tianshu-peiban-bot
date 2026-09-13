"""One-time local source preparation. No service startup or production data copy."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never", GIT_LFS_SKIP_SMUDGE="1")

def git(directory, *args):
    result = subprocess.run(["git", "-C", str(directory), *args], env=ENV,
                            capture_output=True, text=True, encoding="utf-8", timeout=180)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.rstrip("\r\n")

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

def seed(project, target):
    target.mkdir(parents=True, exist_ok=True)
    content = {
        "README.md": f"# {project['name']}\n\n{project['role']}。\n\n当前为 V2 开发准备骨架，未实现业务服务。旧代码来源见主工作区 workspace.json。\n\n本项目有独立 Git；协调检出不供并发写入，任务在主工作区 worktrees 中进行。工作目录上下文见 .runtime/workspace-context.json，或回到主工作区 docs/development/CURRENT.md。\n\n首个任务负责核定实际依赖、安装和检查命令；当前没有可运行应用，不执行生产连接。\n",
        "AGENTS.md": "# 项目开发约定\n\n遵循天枢主工作区的 V2 总稿、当前任务卡及已发布的 contracts 版本。任务 worktree 的 .runtime/workspace-context.json 记录主工作区与明确基线。\n\n只在分配的目录内实施，不改其他项目或共享合同；公共入口、依赖锁和迁移主线单人负责。禁止把未配置服务显示成成功。\n\n当前没有应用安装、构建或测试命令。首个脚手架任务必须从实际 manifest/脚本核定并更新本文件。验证需区分组件、替身联合、真实外部接入。\n\n本地隔离开发和提交用于审查；不自动推送、部署或操作真实设备。交付短记录 docs/handoffs/<任务编号>.md，含实际变更、验证、风险及下一步。\n",
        ".gitignore": ".runtime/\n.env\n.env.*\n!.env.example\n.venv/\nnode_modules/\ndist/\n__pycache__/\n*.pyc\n"
    }
    if not (target/".git").exists():
        for name, text in content.items():
            p = target/name
            if p.exists():
                raise RuntimeError(f"Refusing to overwrite {p}")
            p.write_text(text, encoding="utf-8")
        git(target, "init", "--initial-branch=main")
        git(target, "add", "README.md", "AGENTS.md", ".gitignore")
        git(target, "-c", "user.name=Codex", "-c", "user.email=codex@localhost",
            "commit", "-m", "chore: initialize V2 development preparation")
    return {"baseline_commit":git(target,"rev-parse","HEAD"),"state":"prepared_skeleton"}

def clone(entry, target):
    source = entry["source"]
    if not (target/".git").exists():
        if target.exists() and any(target.iterdir()):
            raise RuntimeError(f"Nonempty target: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        args = ["clone", "--no-checkout"]
        args += ["--depth=1"] if source.startswith("https://") else ["--no-hardlinks"]
        git(ROOT, *args, source, str(target))
        git(target, "-c", "filter.lfs.smudge=", "-c", "filter.lfs.required=false",
            "-c", "filter.lfs.process=", "checkout", "--detach", entry["source_ref"])
        # New independent copies are fetchable, but never accidentally push to originals.
        git(target, "config", "remote.origin.pushurl", "disabled://source-reference")
        if entry["path"].startswith("projects/"):
            git(target, "switch", "-c", "tianshu-integration")
    head = git(target,"rev-parse","HEAD")
    if entry["source_ref"] != "HEAD" and head != entry["source_ref"]:
        raise RuntimeError(f"Unexpected existing checkout: {entry['id']}")
    return {"baseline_commit":head,"state":"prepared_clone","source":source}

def overlay(entry):
    if entry["kind"] != "local_clone":
        return None
    source = Path(entry["source"])
    status = git(source,"status","--porcelain")
    record = {"source":str(source),"source_head":git(source,"rev-parse","HEAD"),
              "status_before":status,"untracked_copied":[],"excluded":[]}
    if status:
        out=ROOT/"references/overlays"/entry["id"]
        if out.exists():
            record["overlay_state"]="existing_overlay_retained"
            return record
        out.mkdir(parents=True)
        for name,args in [("tracked.patch",["diff","--binary","HEAD"]),
                          ("staged.patch",["diff","--binary","--cached"]),
                          ("unstaged.patch",["diff","--binary"])]:
            result=subprocess.run(["git","-C",str(source),*args],capture_output=True,env=ENV,check=True)
            (out/name).write_bytes(result.stdout)
        files=git(source,"ls-files","--others","--exclude-standard","-z").split("\0")
        for name in filter(None,files):
            p=source/name
            if p.is_symlink() or not p.is_file() or p.stat().st_size>20*1024*1024:
                record["excluded"].append(name)
                continue
            dest=out/"untracked"/name
            if not dest.resolve().is_relative_to(out.resolve()):
                raise RuntimeError("Invalid untracked path")
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(p,dest)
            record["untracked_copied"].append(name)
        record["overlay_path"]=out.relative_to(ROOT).as_posix()
        record["status_after"]=git(source,"status","--porcelain")
        record["source_changed_during_capture"]=record["status_after"]!=status
        save(out/"capture.json",record)
    return record

def main():
    manifest=json.loads((ROOT/"workspace.json").read_text(encoding="utf-8"))
    inventory=[]
    for entry in manifest["projects"]+manifest["references"]:
        target=(ROOT/entry["path"]).resolve()
        if not target.is_relative_to(ROOT) or target==ROOT:
            raise RuntimeError("Target outside project workspace")
        print("Preparing "+entry["id"],flush=True)
        result=seed(entry,target) if entry["kind"]=="new" else clone(entry,target)
        entry.update(result)
        captured=overlay(entry)
        inventory.append({"id":entry["id"],"path":entry["path"],**result,"source_worktree":captured})
        save(ROOT/"workspace.json",manifest)
        save(ROOT/"references/source-inventory.json",{"date":"2026-09-14","sources":inventory})
    save(ROOT/"workspace.local.json",{"python":sys.executable,"git":shutil.which("git"),"workspace":str(ROOT)})
    print(json.dumps({"projects":len(manifest["projects"]),"references":len(manifest["references"]),"services_started":0}))

if __name__=="__main__":
    main()
