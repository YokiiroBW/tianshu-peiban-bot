"""Approved editable slots must match the actual reviewed graph, without executing it."""

import unittest
from tianshu.capabilities.images.workflow import Workflow


class WorkflowInspection(unittest.TestCase):
    def test_linked_text_and_wrong_literal_type_are_not_editable(self):
        workflow = Workflow.standard("fixture.safetensors")
        graph = workflow.graph
        graph["2"]["inputs"]["text"] = ["3", 0]
        graph["5"]["inputs"]["steps"] = "24"
        result = Workflow.inspect(graph)
        self.assertFalse(any(slot["node"] == "2" for slot in result["candidates"]["positive"]))
        self.assertEqual(result["candidates"]["steps"], [])
        self.assertEqual(result["outputs"][0]["node"], "7")
        self.assertTrue(any(slot["node"] == "5" for slot in result["candidates"]["seed"]))
