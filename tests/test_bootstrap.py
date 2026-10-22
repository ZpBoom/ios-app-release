import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import release as core

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bootstrap_tests", ROOT / "scripts/bootstrap.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class BootstrapTests(unittest.TestCase):
    def test_configured_modern_ruby_is_reused(self):
        with patch.object(b, "capture", return_value=(True, "ruby 3.3.10")) as capture:
            actual = b.modern_ruby({"ruby_bin": "/custom/ruby/bin/ruby"})
        self.assertEqual(actual, "/custom/ruby/bin/ruby")
        self.assertEqual(capture.call_count, 1)

    def test_no_brew_and_no_modern_ruby_produces_actionable_error(self):
        with patch.object(b, "modern_ruby", return_value=None), patch.object(b, "locate_brew", return_value=None), patch.object(b.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "Homebrew"):
                b.install_fastlane({}, automatic=True)
            run.assert_not_called()

    def test_auto_install_persists_chosen_ruby_and_uses_it_for_bundler(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            settings = {}
            def query(command, env=None):
                if "--prefix" in command:
                    return True, str(config / "ruby")
                if "-e" in command:
                    return True, str(config / "gem-bin")
                return True, "Bundler version 2.6"
            with patch.object(b, "CONFIG", config), patch.object(b, "modern_ruby", side_effect=[None, str(config / "ruby/bin/ruby")]), patch.object(b, "locate_brew", return_value="/brew"), patch.object(b, "capture", side_effect=query), patch.object(b.subprocess, "run") as run:
                b.install_fastlane(settings, automatic=True)
            self.assertEqual(settings["ruby_bin"], str(config / "ruby/bin/ruby"))
            self.assertEqual(settings["ruby_gem_bin"], str(config / "gem-bin"))
            self.assertEqual(run.call_args_list[-1].args[0][0], settings["ruby_bin"])
            self.assertTrue(run.call_args_list[-1].kwargs["env"]["PATH"].startswith(str(config / "ruby/bin")))
            self.assertTrue((config / "runtime/Gemfile").exists())

    def test_missing_bundler_is_installed_using_selected_ruby(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            settings = {}
            with patch.object(b, "CONFIG", config), patch.object(b, "modern_ruby", return_value="/custom/ruby/bin/ruby"), patch.object(b, "capture", side_effect=[(True, str(config / "gems/bin")), (False, "no bundler")]), patch.object(b.subprocess, "run") as run:
                b.install_fastlane(settings)
            command = run.call_args_list[0].args[0]
            self.assertEqual(command[:5], ["/custom/ruby/bin/ruby", "-S", "gem", "install", "bundler"])

    def test_other_chat_reuses_configured_tool_paths_and_xcode(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = {"ruby_bin": "/selected/ruby/bin/ruby", "ruby_gem_bin": "/selected/gems/bin", "developer_dir": "/selected/Xcode/Contents/Developer"}
            with patch.object(core, "CONFIG_HOME", Path(directory)):
                env = core.runtime_env(settings)
            self.assertTrue(env["PATH"].startswith("/selected/ruby/bin:/selected/gems/bin:"))
            self.assertEqual(env["DEVELOPER_DIR"], settings["developer_dir"])

    def test_missing_api_credentials_never_reports_connected(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(b, "CONFIG", Path(directory)), patch.object(b, "capture", return_value=(False, "not ready")), patch.object(b, "modern_ruby", return_value=None), patch.object(b, "locate_codex", return_value=None), patch.dict(b.os.environ, {"ASC_KEY_ID": "", "ASC_KEY_PATH": ""}):
            report = b.setup_report({}, verify_api=True)
        self.assertFalse(report["dependencies_ready"])
        self.assertEqual(report["api"]["status"], "needs_credentials")
        self.assertEqual(report["signing"]["status"], "needs_app_validation")


if __name__ == "__main__":
    unittest.main()
