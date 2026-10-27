import json
import tempfile
import unittest
from pathlib import Path

from deploy.single_container.host_settings import (
    MODULES,
    load_settings,
    module_environment,
)


class HostSettingsTests(unittest.TestCase):
    def test_explicit_module_environments_do_not_inherit_platform_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "memory.env"
            path.write_text(
                "TIANSHU_STORAGE_NAMESPACE='memory-only'\nVALUE='literal $ value'\n"
            )
            base = {
                "PATH": "retained-system-path",
                "USERPROFILE": "retained-home",
                "TIANSHU_DIAGNOSTICS_TOKEN": "platform-only",
                "UNRELATED_SECRET": "excluded",
                "TIANSHU_STORAGE_NAMESPACE": "platform-only",
            }
            spec = {"env_file": [str(path)], "env": {"TOKEN": "memory-only"}}
            result = module_environment(spec, environ=base)
            self.assertEqual(result["TIANSHU_STORAGE_NAMESPACE"], "memory-only")
            self.assertEqual(result["VALUE"], "literal $ value")
            self.assertEqual(result["PATH"], base["PATH"])
            self.assertEqual(result["USERPROFILE"], base["USERPROFILE"])
            self.assertNotIn("TIANSHU_DIAGNOSTICS_TOKEN", result)
            self.assertNotIn("UNRELATED_SECRET", result)
            self.assertEqual(base["TIANSHU_STORAGE_NAMESPACE"], "platform-only")

    def test_read_configuration_leaves_native_config_bytes_and_env_files_untouched(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "native.json"
            original = b'{"native":"unchanged"}\r\n'
            native.write_bytes(original)
            modules = {
                name: {
                    "settings": str(native),
                    "origin": f"https://{name}.internal:8443",
                }
                for name in MODULES
            }
            modules["knowledge"]["client"] = "existing-client"
            raw = {
                "version": 1,
                "state_file": str(root / "state.json"),
                "modules": modules,
            }
            config = root / "host.json"
            config.write_text(json.dumps(raw))
            parsed = load_settings(config)
            self.assertEqual(tuple(parsed["modules"]), MODULES)
            self.assertEqual(parsed["shutdown_seconds"], 30)
            self.assertEqual(native.read_bytes(), original)
            self.assertEqual(parsed["modules"]["platform"]["env"], {})
            for mutate in (
                lambda value: value.update(version=True),
                lambda value: value["modules"].pop("memory"),
                lambda value: value["modules"]["knowledge"].pop("client"),
                lambda value: value["modules"]["gateway"].update(
                    origin="https://gateway/v1"
                ),
                lambda value: value["modules"]["companion"].update(
                    settings="relative.json"
                ),
            ):
                candidate = json.loads(json.dumps(raw))
                mutate(candidate)
                config.write_text(json.dumps(candidate))
                with self.assertRaises(ValueError):
                    load_settings(config)
