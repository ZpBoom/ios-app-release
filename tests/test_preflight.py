import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import preflight as p
import release as core

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("project_entry", ROOT / "scripts/project.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def version(number, state, identity="v1"):
    return {"id": identity, "attributes": {"versionString": number, "appStoreState": state}}


class PreflightTests(unittest.TestCase):
    def test_first_release_requires_existing_app_record_but_no_published_history(self):
        result = p.classify([version("1.0", "PREPARE_FOR_SUBMISSION")], "1.0")
        self.assertEqual(result["release_kind"], "FIRST_RELEASE")
        self.assertEqual(result["version_action"], "REUSE_DRAFT")

    def test_update_classification_supports_current_state_and_numeric_order(self):
        rows = [version("1.9.0", "READY_FOR_SALE"), version("1.10.0", "READY_FOR_DISTRIBUTION", "v2")]
        result = p.classify(rows, "1.11.0")
        self.assertEqual(result["release_kind"], "UPDATE")
        self.assertEqual(result["latest_published"]["id"], "v2")
        self.assertEqual(result["version_action"], "CREATE_VERSION")

    def test_published_version_and_older_version_are_not_overwritten(self):
        rows = [version("2.0.0", "READY_FOR_SALE")]
        self.assertEqual(p.classify(rows, "2.0.0")["version_action"], "ALREADY_RELEASED")
        self.assertEqual(p.classify(rows, "1.9.0")["version_action"], "VERSION_NOT_NEWER")

    def test_other_inflight_version_blocks_creating_new_version(self):
        rows = [version("1.0", "READY_FOR_SALE"), version("1.1", "IN_REVIEW", "v2")]
        self.assertEqual(p.classify(rows, "1.2")["version_action"], "OTHER_VERSION_IN_PROGRESS")

    def test_pending_approval_is_distinct_from_preparing_upload(self):
        result = p.classify([version("1.0", "PENDING_DEVELOPER_RELEASE")], "1.0")
        self.assertEqual(result["version_action"], "READY_TO_RELEASE")

    def test_absent_visible_app_is_unknown_instead_of_automatic_new_app(self):
        api = Mock()
        api.list.return_value = []
        report = {"checks": []}
        p.remote(api, {"bundle_id": "com.test.app"}, "1.0", "1", report)
        self.assertEqual(report["online"]["release_kind"], "UNKNOWN")
        self.assertEqual(report["online"]["version_action"], "APP_RECORD_NOT_VISIBLE")
        api.request.assert_not_called()

    def test_account_identity_drift_is_blocked(self):
        api = Mock()
        api.list.return_value = [{"id": "different-id"}]
        report = {"checks": []}
        p.remote(api, {"bundle_id": "com.test.app"}, "1.0", "1", report, expected_id="expected-id")
        self.assertEqual(report["checks"][0]["level"], "blocker")
        self.assertEqual(api.list.call_count, 1)

    def test_multiple_schemes_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "App.xcodeproj").mkdir()
            report = {"checks": []}
            with patch.object(p, "xcode_json", return_value={"project": {"schemes": ["App", "AppTests"]}}) as query:
                identity = p.discover(project, report)
            self.assertIsNone(identity)
            self.assertEqual(query.call_count, 1)
            self.assertEqual(report["checks"][-1]["code"], "scheme_selection")

    def test_discovers_main_ios_app_without_selecting_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "App.xcodeproj/project.xcworkspace").mkdir(parents=True)
            report = {"checks": []}
            settings = [{"target": "Widget", "buildSettings": {"PRODUCT_TYPE": "com.apple.product-type.app-extension", "PLATFORM_NAME": "iphoneos"}},
                {"target": "App", "buildSettings": {"PRODUCT_TYPE": "com.apple.product-type.application",
                 "SUPPORTED_PLATFORMS": "iphonesimulator iphoneos", "PRODUCT_BUNDLE_IDENTIFIER": "com.test.app",
                 "DEVELOPMENT_TEAM": "ABCDE12345", "MARKETING_VERSION": "1.1", "CURRENT_PROJECT_VERSION": "12"}}]
            with patch.object(p, "xcode_json", side_effect=[{"project": {"schemes": ["App"]}}, settings]):
                identity = p.discover(project, report)
            self.assertEqual(identity["bundle_id"], "com.test.app")
            self.assertEqual(report["xcode_candidates"]["workspaces"], [])

    def test_network_failure_cannot_report_first_release(self):
        api = Mock()
        api.list.side_effect = core.ReleaseError("network failure")
        identity = {"bundle_id": "com.test.app", "version": "1.1", "build": "12"}
        with tempfile.TemporaryDirectory() as directory, patch.object(p, "discover", return_value=identity):
            report = p.report_project(Path(directory), api=api)
        self.assertEqual(report["online"]["release_kind"], "UNKNOWN")
        self.assertFalse(report["ready_for_prepare"])

    def test_bind_creates_portable_project_config_without_reusing_old_release_notes(self):
        report = {"identity": {"bundle_id": "com.test.app", "team_id": "ABCDE12345", "scheme": "App",
                  "target": "App", "xcode": "App.xcodeproj", "signing_style": "Automatic"},
                  "online": {"app_store_id": "123", "primary_locale": "en-US", "localizations": {
                      "en-US": {"description": "Existing description", "supportUrl": "https://example.com", "whatsNew": "Previous release"}}},
                  "desired": {"version": "1.1", "build": "12"}}
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            entry.bind(project, report)
            c, r = entry.configs(project)
            self.assertEqual(c["source"], ".")
            self.assertEqual(c["source_base"], "project")
            self.assertEqual(r["localizations"]["en-US"]["whatsNew"], "")
            self.assertEqual(r["localizations"]["en-US"]["description"], "Existing description")
            self.assertIn(".ios-release/reports/", (project / ".gitignore").read_text())
            with self.assertRaises(core.ReleaseError):
                entry.bind(project, report)

    def test_portable_source_resolution_uses_app_project_not_plugin_or_other_machine(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "App.xcodeproj").mkdir()
            c = {"source": ".", "source_base": "project", "project": "App.xcodeproj"}
            with patch.object(core, "ROOT", project / ".ios-release"):
                actual = core.runtime_config("app", c, {"version": "1.0", "build": "1"}, {"source_root": "/unrelated"})
            self.assertEqual(actual["source"], str(project.resolve()))
            self.assertTrue(actual["output"].startswith(str(project / ".ios-release/.state")))

    def test_complete_config_and_remote_inherited_metadata_can_prepare_update(self):
        api = Mock()
        api.list.side_effect = [[{"id": "123", "attributes": {"name": "App", "primaryLocale": "en-US"}}],
            [version("1.0", "READY_FOR_SALE")],
            [{"id": "loc", "attributes": {"locale": "en-US", "description": "Existing", "supportUrl": "https://example.com"}}], []]
        identity = {"bundle_id": "com.test.app", "version": "1.1", "build": "12", "team_id": "ABCDE12345"}
        config = {"bundle_id": "com.test.app", "team_id": "ABCDE12345", "app_store_id": "123", "locales": ["en-US"]}
        release = {"version": "1.1", "build": "12", "localizations": {"en-US": {"whatsNew": "New fixes."}}}
        with tempfile.TemporaryDirectory() as directory, patch.object(p, "discover", return_value=identity):
            report = p.report_project(Path(directory), api=api, config=config, release=release)
        self.assertTrue(report["ready_for_prepare"])
        self.assertTrue(report["coverage"]["not_verified"])
        api.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
