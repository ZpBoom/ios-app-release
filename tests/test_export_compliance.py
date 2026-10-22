import contextlib
import io
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import Mock, patch

import preflight
import release


def build(value=None):
    return {"id": "b1", "attributes": {"processingState": "VALID", "expired": False,
                                       "usesNonExemptEncryption": value}}


class ExportComplianceTests(unittest.TestCase):
    def test_only_boolean_false_is_accepted_as_exempt_without_document_lookup(self):
        api = Mock()
        self.assertTrue(release.build_export_compliance(api, build(False))["ready"])
        for value in (None, "false", "NO", 0, 1):
            with self.subTest(value=value):
                self.assertFalse(release.build_export_compliance(api, build(value))["ready"])
        api.request.assert_not_called()

    def test_non_exempt_build_requires_approved_linked_document(self):
        api = Mock()
        for state in ("APPROVED", "CREATED", "IN_REVIEW", "REJECTED", "EXPIRED", "INVALID", None, "FUTURE_STATE"):
            with self.subTest(state=state):
                api.request.return_value = {"data": {"id": "d1", "attributes": {
                    "appEncryptionDeclarationState": state}}}
                result = release.build_export_compliance(api, build(True))
                self.assertEqual(result["ready"], state == "APPROVED")
                self.assertEqual(result["declaration_state"], state)
        self.assertTrue(all(c.args == ("GET", "/v1/builds/b1/appEncryptionDeclaration") for c in api.request.call_args_list))

    def test_unlinked_document_and_unavailable_api_cannot_pass(self):
        api = Mock()
        api.request.return_value = {"data": None}
        self.assertEqual(release.build_export_compliance(api, build(True))["status"], "DOCUMENT_MISSING")
        api.request.side_effect = release.ReleaseError("Apple API 500")
        self.assertEqual(release.build_export_compliance(api, build(True))["status"], "UNKNOWN")

    def submit(self, api, current_build):
        version = {"id": "v1", "attributes": {"appStoreState": "PREPARE_FOR_SUBMISSION", "releaseType": "MANUAL"}}
        with patch.object(release, "remote_version", return_value=version), patch.object(
                release, "differences", return_value=({}, [])), patch.object(
                release, "remote_build", return_value=current_build), patch.object(release, "fastlane") as lane:
            try:
                release.submit(api, "demo", {}, {}, {})
            finally:
                self.lane_calls = lane.call_args_list

    def test_submit_rejects_missing_or_unapproved_compliance_before_any_write(self):
        for value, response in [(None, None), (True, None), (True, {"id": "d1", "attributes": {
                "appEncryptionDeclarationState": "IN_REVIEW"}})]:
            with self.subTest(value=value, response=response):
                api = Mock()
                api.request.return_value = {"data": response}
                with self.assertRaisesRegex(release.ReleaseError, "出口合规检查未通过"):
                    self.submit(api, build(value))
                self.assertEqual(self.lane_calls, [])
                self.assertFalse(any(c.args[0] != "GET" for c in api.request.call_args_list))

    def test_submit_proceeds_for_exempt_or_approved_build_without_changing_declaration(self):
        for value in (False, True):
            with self.subTest(value=value):
                api = Mock()
                api.request.return_value = {"data": {"id": "d1", "attributes": {
                    "appEncryptionDeclarationState": "APPROVED"}}}
                self.submit(api, build(value))
                self.assertEqual(self.lane_calls[0].args[0], "submit")
                writes = [c for c in api.request.call_args_list if c.args[0] != "GET"]
                self.assertEqual(len(writes), 1)
                self.assertEqual(writes[0].args[:2], ("PATCH", "/v1/appStoreVersions/v1"))
                self.assertNotIn("attributes", writes[0].args[2]["data"])

    def test_remote_check_reports_submission_blocker_but_preserves_metadata_recovery(self):
        for value in (None, False, True):
            with self.subTest(value=value):
                api = Mock()
                api.list.side_effect = [[{"id": "app1", "attributes": {"name": "Demo"}}],
                    [{"id": "v1", "attributes": {"versionString": "1.2", "appStoreState": "PREPARE_FOR_SUBMISSION"}}],
                    [{"attributes": {"locale": "en-US", "description": "Existing", "supportUrl": "https://example.com"}}],
                    [build(value)]]
                api.request.side_effect = release.ReleaseError("permission denied")
                identity = {"bundle_id": "com.example.app", "team_id": "ABCDE12345", "version": "1.2", "build": "1"}
                config = {"bundle_id": "com.example.app", "team_id": "ABCDE12345", "locales": ["en-US"]}
                notes = {"localizations": {"en-US": {"whatsNew": "Fixes"}}}
                with tempfile.TemporaryDirectory() as directory, patch.object(preflight, "discover", return_value=identity):
                    project = Path(directory)
                    report = preflight.report_project(project, api=api, config=config, release=notes)
                    self.assertTrue(report["ready_for_prepare"])
                    self.assertEqual(bool(report["submission_blockers"]), value is not False)
                    self.assertIs(report["online"]["matching_builds"][0]["usesNonExemptEncryption"], value)
                    with contextlib.redirect_stdout(io.StringIO()):
                        preflight.save_report(project, report)
                    saved = json.loads((project / ".ios-release/reports/preflight.json").read_text())
                    html = (project / ".ios-release/reports/preflight.html").read_text()
                    self.assertEqual(saved["submission_blockers"], report["submission_blockers"])
                    self.assertIn("出口合规提审阻塞项", html)
                    if value is None:
                        self.assertIn("无需重新上传", html)
                self.assertFalse(any(c.args[0] != "GET" for c in api.request.call_args_list))

    def test_local_check_uses_selected_target_plist_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "App").mkdir()
            selected = project / "App/Info.plist"
            selected.write_bytes(plistlib.dumps({"ITSAppUsesNonExemptEncryption": False}))
            (project / "Info.plist").write_bytes(plistlib.dumps({"ITSAppUsesNonExemptEncryption": True}))
            before = selected.read_bytes()
            report = {"checks": []}
            preflight.local_export_compliance(project, "App.xcodeproj", {
                "SRCROOT": str(project), "INFOPLIST_FILE": "$(SRCROOT)/App/Info.plist"}, report)
            check = report["checks"][0]
            self.assertEqual(check["level"], "pass")
            self.assertIs(check["evidence"]["ITSAppUsesNonExemptEncryption"], False)
            self.assertEqual(selected.read_bytes(), before)

    def test_generated_setting_can_declare_no_without_a_source_plist(self):
        report = {"checks": []}
        preflight.local_export_compliance(Path("/unused"), "App.xcodeproj", {
            "GENERATE_INFOPLIST_FILE": "YES", "INFOPLIST_FILE": "nonexistent.plist",
            "INFOPLIST_KEY_ITSAppUsesNonExemptEncryption": "NO"}, report)
        self.assertEqual(report["checks"][0]["level"], "pass")
        self.assertIs(report["checks"][0]["evidence"]["ITSAppUsesNonExemptEncryption"], False)

    def test_missing_or_string_plist_declaration_warns_without_autofill(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            path = project / "Info.plist"
            for value in (None, "NO", 0):
                path.write_bytes(plistlib.dumps({} if value is None else {"ITSAppUsesNonExemptEncryption": value}))
                before = path.read_bytes()
                report = {"checks": []}
                preflight.local_export_compliance(project, "App.xcodeproj", {
                    "SRCROOT": str(project), "INFOPLIST_FILE": "Info.plist"}, report)
                self.assertEqual(report["checks"][0]["level"], "warning")
                self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
