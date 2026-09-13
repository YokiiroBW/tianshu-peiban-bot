"""Small coordinator utility: inspect the workspace and allocate isolated tasks."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def load(path):
    return json.loads(path.read_text(encoding="utf-8"))

def save(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temporary.replace(path)

def git(repo,*args):
    r=subprocess.run(["git","-C",str(repo),*args],capture_output=True,text=True,encoding="utf-8")
    if r.returncode:
        raise ValueError(r.stderr.strip())
    return r.stdout.strip()

def inside(root, relative, prefix=None):
    root=root.resolve()
    candidate=(root/relative).resolve()
    boundary=(root/prefix).resolve() if prefix else root
    if not candidate.is_relative_to(boundary) or candidate==root:
        raise ValueError("Path leaves its allowed workspace: "+relative)
    return candidate

def validate_tasks(tasks, project_ids):
    ids=[t["id"] for t in tasks]
    if len(ids)!=len(set(ids)):
        raise ValueError("Duplicate task ID")
    by_id={t["id"]:t for t in tasks}
    for t in tasks:
        if not re.fullmatch(r"TS-\d{3}",t["id"]):
            raise ValueError("Invalid task ID")
        if t["project"] not in project_ids or not t["allowed_paths"]:
            raise ValueError("Task project or scope missing: "+t["id"])
        if t["status"] not in {"planned","ready","allocated","in_progress","review","done","blocked"}:
            raise ValueError("Invalid task status")
        if any(d not in by_id for d in t["depends_on"]):
            raise ValueError("Unknown dependency: "+t["id"])
    visited=set()
    active=set()
    def visit(task_id):
        if task_id in active: raise ValueError("Dependency cycle")
        if task_id in visited: return
        active.add(task_id)
        for dependency in by_id[task_id]["depends_on"]: visit(dependency)
        active.remove(task_id);visited.add(task_id)
    for task_id in ids: visit(task_id)
    return by_id

def allocation_plan(root,manifest,board,task_id):
    projects={p["id"]:p for p in manifest["projects"]}
    tasks=validate_tasks(board["tasks"],set(projects)|{"workspace"})
    if task_id not in tasks: raise ValueError("Unknown task")
    task=tasks[task_id]
    if task["status"] not in {"planned","ready"}:
        raise ValueError("Task is already allocated or not ready")
    unmet=[d for d in task["depends_on"] if tasks[d]["status"]!="done"]
    if unmet: raise ValueError("Unmet dependencies: "+", ".join(unmet))
    assigned=sum(t["status"] in {"allocated","in_progress","review"} for t in tasks.values())
    if assigned>=manifest["parallel_workers"]:
        raise ValueError("Parallel worker capacity reached")
    repo=root if task["project"]=="workspace" else inside(root,projects[task["project"]]["path"],"projects")
    destination=inside(root,f"worktrees/{task_id}/{repo.name if repo!=root else 'workspace'}","worktrees")
    if destination.exists(): raise ValueError("Worktree destination already exists")
    return task,repo,destination,"work/"+task_id.lower()

def start(root,manifest,board,task_id):
    runtime=root/".runtime";runtime.mkdir(exist_ok=True)
    lock=runtime/"allocation.lock"
    try:
        descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("Allocation is locked; inspect the prior process before recovery") from exc
    try:
        with os.fdopen(descriptor,"w") as f:f.write(str(os.getpid()))
        # Read fresh state after taking the allocator lock.
        board=load(root/"docs/development/tasks.json")
        task,repo,destination,branch=allocation_plan(root,manifest,board,task_id)
        baseline=git(repo,"rev-parse","HEAD")
        if git(repo,"status","--porcelain"):
            raise ValueError("Coordinator checkout is dirty; review and commit the baseline first")
        destination.parent.mkdir(parents=True,exist_ok=True)
        git(repo,"worktree","add","-b",branch,str(destination),baseline)
        save(destination/".runtime/workspace-context.json",{
            "workspace":str(root),"task":task_id,"project":task["project"],
            "baseline_commit":baseline,"contract_state":"use only explicitly published versions",
            "scope":task["allowed_paths"]})
        task.update(status="allocated",worktree=destination.relative_to(root).as_posix(),branch=branch,
                    baseline_commit=baseline,assignee="unassigned")
        save(root/"docs/development/tasks.json",board)
        leases_path=runtime/"workspace-leases.json"
        leases=load(leases_path) if leases_path.exists() else {"tasks":{}}
        leases["tasks"][task_id]={"repo":str(repo),"path":str(destination),"branch":branch,"baseline":baseline}
        save(leases_path,leases)
        return {"task":task_id,"state":"allocated_not_running","worktree":str(destination),"branch":branch}
    finally:
        lock.unlink()

def check(root,manifest,board):
    ids=[p["id"] for p in manifest["projects"]]
    validate_tasks(board["tasks"],set(ids)|{"workspace"})
    all_entries=manifest["projects"]+manifest["references"]
    if len({e["path"] for e in all_entries})!=len(all_entries): raise ValueError("Duplicate repository path")
    results=[]
    for entry in all_entries:
        prefix="projects" if entry in manifest["projects"] else "references"
        repo=inside(root,entry["path"],prefix)
        if Path(git(repo,"rev-parse","--show-toplevel")).resolve()!=repo:
            raise ValueError("Not an independent repository: "+entry["id"])
        common=Path(git(repo,"rev-parse","--path-format=absolute","--git-common-dir")).resolve()
        if not common.is_relative_to(repo): raise ValueError("Repository borrows external Git state")
        if (common/"objects/info/alternates").exists(): raise ValueError("Repository borrows external objects")
        git(repo,"cat-file","-e",entry["baseline_commit"]+"^{commit}")
        for source in entry.get("verification_sources",[]):
            if not (repo/source).exists(): raise ValueError("Missing command source: "+str(repo/source))
        results.append({"id":entry["id"],"path":entry["path"],"head":git(repo,"rev-parse","HEAD")})
    for task in board["tasks"]:
        if task["status"] in {"ready","allocated","in_progress","review"} and task["id"]!="TS-000":
            by_id={t["id"]:t for t in board["tasks"]}
            if any(by_id[d]["status"]!="done" for d in task["depends_on"]):
                raise ValueError("Active task has unmet dependencies: "+task["id"])
        if task.get("worktree"):
            wt=inside(root,task["worktree"],"worktrees")
            if not (wt/".git").is_file(): raise ValueError("Missing isolated worktree")
            if git(wt,"branch","--show-current")!=task["branch"]: raise ValueError("Wrong task branch")
            if not (wt/".runtime/workspace-context.json").exists(): raise ValueError("Missing task context")
    for needed in ["AGENTS.md","README.md","contracts/README.md","docs/development/CURRENT.md",
                   "docs/development/parallel-development-plan.md"]:
        if not (root/needed).is_file(): raise ValueError("Missing entry document: "+needed)
    return {"status":"passed","scope":"workspace preparation only","repositories":results,
            "tasks":len(board["tasks"]),"application_tests_run":False}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["check","status","start"])
    parser.add_argument("task",nargs="?")
    args=parser.parse_args()
    manifest=load(ROOT/"workspace.json");board=load(ROOT/"docs/development/tasks.json")
    try:
        if args.command=="start":
            if not args.task: parser.error("start requires a task ID")
            result=start(ROOT,manifest,board,args.task)
        elif args.command=="check": result=check(ROOT,manifest,board)
        else:
            result={"phase":manifest["phase"],"tasks":[{k:t.get(k) for k in ["id","title","status","depends_on","worktree"]} for t in board["tasks"]]}
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,KeyError,OSError) as exc:
        print(json.dumps({"status":"failed","reason":str(exc)},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)

if __name__=="__main__":main()
