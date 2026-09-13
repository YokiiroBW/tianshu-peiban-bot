import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location("workspace",Path(__file__).resolve().parents[2]/"scripts/workspace.py")
workspace=importlib.util.module_from_spec(spec);spec.loader.exec_module(workspace)

class WorkspaceRules(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.manifest={"parallel_workers":3,"projects":[{"id":"core","path":"projects/core"}]}
        self.base={"id":"TS-000","project":"workspace","status":"done","depends_on":[],"allowed_paths":["docs/"]}
        self.work={"id":"TS-001","project":"core","status":"ready","depends_on":["TS-000"],"allowed_paths":["src/"]}
        self.board={"tasks":[self.base,self.work]}

    def test_ready_task_has_separate_worktree(self):
        _,repo,dest,branch=workspace.allocation_plan(self.root,self.manifest,self.board,"TS-001")
        self.assertEqual(repo,self.root/"projects/core")
        self.assertEqual(dest,self.root/"worktrees/TS-001/core")
        self.assertEqual(branch,"work/ts-001")
        self.assertNotEqual(repo,dest)

    def test_unmet_dependency_refuses_allocation(self):
        self.base["status"]="in_progress"
        with self.assertRaisesRegex(ValueError,"Unmet dependencies"):
            workspace.allocation_plan(self.root,self.manifest,self.board,"TS-001")

    def test_duplicate_allocation_refused(self):
        self.work["status"]="allocated"
        with self.assertRaisesRegex(ValueError,"already allocated"):
            workspace.allocation_plan(self.root,self.manifest,self.board,"TS-001")

    def test_existing_directory_never_overwritten(self):
        (self.root/"worktrees/TS-001/core").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,"already exists"):
            workspace.allocation_plan(self.root,self.manifest,self.board,"TS-001")

    def test_path_escape_refused(self):
        for relative in ["../outside","projects/../../outside"]:
            with self.assertRaises(ValueError):workspace.inside(self.root,relative,"projects")

    def test_cycles_and_missing_dependencies_refused(self):
        self.base["depends_on"]=["TS-001"]
        with self.assertRaisesRegex(ValueError,"cycle"):
            workspace.validate_tasks(self.board["tasks"],{"workspace","core"})
        self.base["depends_on"]=["TS-099"]
        with self.assertRaisesRegex(ValueError,"Unknown dependency"):
            workspace.validate_tasks(self.board["tasks"],{"workspace","core"})

    def test_capacity_refused(self):
        self.manifest["parallel_workers"]=0
        with self.assertRaisesRegex(ValueError,"capacity"):
            workspace.allocation_plan(self.root,self.manifest,self.board,"TS-001")

if __name__=="__main__":unittest.main()
