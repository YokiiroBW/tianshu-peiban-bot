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

    def router_workflow(self, template):
        graph = {
            "11": {"class_type": "AnimaPromptPlusClipEncode", "inputs": {
                "artist_tags": ["31", 0], "character_tags": ["32", 0],
                "clothing_tags": ["36", 0], "pose_tags": ["36", 1],
                "background_tags": ["36", 2], "extra_prompt": ["36", 3],
            }},
            "12": {"class_type": "ResolutionMaster", "inputs": {"width": 1024, "height": 1536}},
            "17": {"class_type": "CLIPTextEncode", "inputs": {"text": "authored negative"}},
            "20": {"class_type": "SaveImage", "inputs": {"images": ["24", 0]}},
            "24": {"class_type": "KSampler", "inputs": {
                "positive": ["11", 0], "negative": ["17", 0], "seed": 123, "steps": 20,
                "model": ["27", 0],
            }},
            "27": {"class_type": "LoraLoaderModelOnly", "inputs": {
                "lora_name": "synthetic-character.safetensors", "strength_model": 0.8,
            }},
            "31": {"class_type": "AnimaArtistTagSelectorPlus", "inputs": {
                "artist_tags": "authored artist",
            }},
            "32": {"class_type": "AnimaCharacterTagSelectorPlus", "inputs": {
                "character_tags": "authored fictional appearance",
            }},
            "35": {"class_type": "Simple String", "inputs": {"text": template}},
            "36": {"class_type": "AstrBot Prompt Router", "inputs": {"prompt": ["35", 0]}},
        }
        bindings = {semantic: {"node": "35", "class_type": "Simple String", "input": "text"}
                    for semantic in ("positive", "outfit", "pose", "background", "camera")}
        bindings["negative"] = {
            "node": "17", "class_type": "CLIPTextEncode", "input": "text", "mode": "append",
        }
        for semantic in ("width", "height"):
            bindings[semantic] = {
                "node": "12", "class_type": "ResolutionMaster", "input": semantic, "min": 64, "max": 4096,
            }
        return Workflow(graph, bindings, ["20"])

    def test_router_preserves_authored_character_while_using_current_scene(self):
        workflow = self.router_workflow(
            "[User image request]\nauthored framing\n\n[Scene, style and final preset]\ndefault scene"
        )
        graph, bindings = workflow.graph, workflow.bindings
        intent = {
            "character": "authored fictional appearance", "outfit": "current coat", "pose": "seated",
            "scene": "current room", "camera": "eye level", "local_time": "2026-11-02T21:00:00+08:00",
            "light": "night lamp", "weather": "rain", "activity": "reading",
            "negative_constraints": ["wrong clothes"], "references": [],
        }
        original = canonical(graph)
        compiled = Engines.compile({"engine": "comfyui", "parameters": {"width": 1024, "height": 1536}}, intent, {
            "graph": graph, "bindings": bindings, "outputs": ["20"],
        })
        rendered = compiled["graph"]
        prompt = rendered["35"]["inputs"]["text"]
        for value in ("authored framing", intent["local_time"], "weather: rain", "light: night lamp",
                      "[Reference and wardrobe ruling]\ncurrent coat",
                      "[Composition and continuity]\nseated",
                      "[Scene, style and final preset]\ncurrent room"):
            self.assertIn(value, prompt)
        self.assertEqual(rendered["17"]["inputs"]["text"], "authored negative\nwrong clothes")
        for node in ("11", "12", "20", "24", "27", "31", "32", "36"):
            self.assertEqual(rendered[node], graph[node])
        self.assertEqual(canonical(graph), original)

    def test_router_replaces_unsectioned_scene_without_an_outfit_override(self):
        workflow = self.router_workflow("standing on an old ocean balcony, old morning light")
        original = canonical(workflow.graph)
        intent = {
            "character": "authored fictional appearance", "outfit": "",
            "pose": "sitting at a desk beside a sketchbook", "scene": "current indoor studio",
            "camera": "close selfie", "activity": "drawing a sketch",
            "requested_change": "show the current sketching activity",
            "negative_constraints": [], "references": [],
        }
        compiled = Engines.compile({"engine": "comfyui"}, intent, {
            "graph": workflow.graph, "bindings": workflow.bindings, "outputs": workflow.outputs,
        })
        prompt = compiled["graph"]["35"]["inputs"]["text"]
        for value in (intent["pose"], intent["scene"], intent["requested_change"], intent["activity"]):
            self.assertIn(value, prompt)
        self.assertNotIn("old ocean balcony", prompt)
        self.assertNotIn("standing", prompt)
        self.assertNotIn("old morning light", prompt)
        for node in workflow.graph.keys() - {"35"}:
            self.assertEqual(compiled["graph"][node], workflow.graph[node])
        self.assertEqual(compiled["outputs"], ["20"])
        self.assertEqual(canonical(workflow.graph), original)

    def test_router_keeps_explicit_defaults_and_replaces_only_current_sections(self):
        workflow = self.router_workflow(
            "[User image request]\nauthored ink style\n\n"
            "[Reference and wardrobe ruling]\nauthored coat default\n\n"
            "[Composition and continuity]\nold default pose\n\n"
            "[Scene, style and final preset]\nold default scene"
        )
        rendered = workflow.render({
            "positive": "current user request", "pose": "current desk pose", "background": "current room",
        })
        prompt = rendered["35"]["inputs"]["text"]
        for value in ("authored ink style", "[Reference and wardrobe ruling]\nauthored coat default",
                      "current user request", "[Composition and continuity]\ncurrent desk pose",
                      "[Scene, style and final preset]\ncurrent room"):
            self.assertIn(value, prompt)
        self.assertNotIn("old default pose", prompt)
        self.assertNotIn("old default scene", prompt)
        for node in ("11", "24", "27", "31", "32", "36"):
            self.assertEqual(rendered[node], workflow.graph[node])
