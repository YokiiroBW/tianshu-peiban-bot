"""Approved editable slots must match the actual reviewed graph, without executing it."""

import unittest
from tianshu.capabilities.contracts import Fault, canonical
from tianshu.capabilities.images.engines import Engines
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

    def test_compiled_multiline_prompt_preserves_reviewed_graph(self):
        workflow = Workflow.standard("fixture.safetensors")
        before = canonical(workflow.graph)
        intent = {
            "character": "fictional character", "outfit": "plain shirt", "pose": "seated",
            "scene": "studio\nwindow light", "camera": "eye level", "references": [],
            "negative_constraints": ["blur\nwrong clothing"],
        }
        compiled = Engines.compile({"engine": "comfyui"}, intent, {
            "graph": workflow.graph, "bindings": workflow.bindings, "outputs": workflow.outputs,
        })
        positive = compiled["graph"]["2"]["inputs"]["text"]
        self.assertTrue(positive.startswith(workflow.graph["2"]["inputs"]["text"] + "\n"))
        self.assertIn(compiled["prompt"], positive)
        self.assertIn(intent["scene"], positive)
        self.assertEqual(compiled["graph"]["3"]["inputs"]["text"], "blur\nwrong clothing")
        for node in ("1", "4", "5", "6", "7"):
            self.assertEqual(compiled["graph"][node], workflow.graph[node])
        self.assertEqual(canonical(workflow.graph), before)

    def test_prompt_line_breaks_preserve_type_length_and_control_guards(self):
        workflow = Workflow.standard("fixture.safetensors", edit=True)
        multiline = "line one\r\nline two"
        rendered = workflow.render({"positive": multiline})
        self.assertTrue(rendered["2"]["inputs"]["text"].endswith(multiline))
        for values in ({"positive": 1}, {"positive": ""}, {"positive": "  "},
                       {"positive": "x" * 8001}, {"positive": "text\x00"},
                       {"positive": "text\x0b"}, {"positive": "text\tindent"},
                       {"reference": "image\nname.png"}):
            with self.subTest(values=values), self.assertRaises(Fault):
                workflow.render(values)
        with self.assertRaises(Fault):
            Workflow.standard("fixture\n.safetensors")
