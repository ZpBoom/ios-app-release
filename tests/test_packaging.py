import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_plugin", ROOT / "scripts/package_plugin.py")
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


class PackagingTests(unittest.TestCase):
    def test_package_contains_runtime_and_onboarding_but_no_machine_data(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "plugin"
            files = pack.package(target)
            self.assertIn("release.py", files)
            self.assertIn("preflight.py", files)
            self.assertIn("scripts/project.py", files)
            self.assertIn("skills/ios-release-setup/SKILL.md", files)
            self.assertFalse(any(x.startswith(("apps/", ".venv/", ".state/", ".git/")) or x.endswith(".p8") or ".machine.json" in x for x in files))
            manifest = json.loads((target / "plugin.json").read_text())
            self.assertTrue((target / manifest["extensions"]["com.openai"]["onboardingSkill"]).exists())

    def test_installed_package_runs_from_unrelated_project_and_reports_uncertainty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "installed-plugin"
            app = root / "separate-app"
            app.mkdir()
            pack.package(package)
            env = os.environ.copy()
            env["IOS_RELEASE_CONFIG_HOME"] = str(root / "personal-config")
            result = subprocess.run([sys.executable, str(package / "scripts/project.py"), "inspect", "--offline"],
                cwd=app, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            report = json.loads((app / ".ios-release/reports/preflight.json").read_text())
            self.assertEqual(report["online"]["release_kind"], "UNKNOWN")
            self.assertFalse(report["ready_for_prepare"])
            self.assertFalse((package / ".ios-release").exists())

    def test_bootstrap_keeps_personal_config_outside_package_and_preserves_existing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "personal"
            config.mkdir()
            machine = config / "machine.json"
            machine.write_text('{"key_id":"existing-value"}')
            env = os.environ.copy()
            env["IOS_RELEASE_CONFIG_HOME"] = str(config)
            result = subprocess.run([sys.executable, str(ROOT / "scripts/bootstrap.py"), "--init-config"],
                env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(machine.read_text())["key_id"], "existing-value")
            self.assertEqual(json.loads((config / "installation.json").read_text())["plugin_root"], str(ROOT))


if __name__ == "__main__":
    unittest.main()
