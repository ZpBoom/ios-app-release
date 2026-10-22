import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release", ROOT / "release.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
C = {"bundle_id": "com.example.MyApp", "app_store_id": "1234567890"}
R = {"version": "1.2.0", "build": "120", "localizations": {"en-US": {"whatsNew": "Fixed bugs."}}}


def version(state="PREPARE_FOR_SUBMISSION", mode="MANUAL"):
    return {"id": "v1", "attributes": {"appStoreState": state, "releaseType": mode}}


class PublishingTests(unittest.TestCase):
    def test_identity_mismatch_stops_before_writes(self):
        api = Mock()
        api.list.return_value = [{"id": "999"}]
        with self.assertRaises(m.ReleaseError):
            m.metadata(api, C, R)
        api.request.assert_not_called()

    def test_metadata_preserves_unspecified_fields_and_sets_manual(self):
        api = Mock()
        api.list.return_value = [{"id": "loc1", "attributes": {
            "locale": "en-US", "description": "Existing description", "whatsNew": "Old text"}}]
        with patch.object(m, "remote_version", return_value=version()):
            m.metadata(api, C, R)
        calls = api.request.call_args_list
        self.assertEqual(calls[0].args[2]["data"]["attributes"], {"releaseType": "MANUAL"})
        self.assertEqual(calls[1].args[2]["data"]["attributes"], R["localizations"]["en-US"])

    def test_uneditable_version_blocks_all_writes(self):
        api = Mock()
        with patch.object(m, "remote_version", return_value=version("IN_REVIEW")):
            with self.assertRaises(m.ReleaseError):
                m.metadata(api, C, R)
        api.request.assert_not_called()

    def test_release_requires_apple_approval_and_matching_build(self):
        for state, build in [("IN_REVIEW", "120"), ("PENDING_DEVELOPER_RELEASE", "119")]:
            api = Mock()
            api.request.return_value = {"data": {"attributes": {"version": build}}}
            with patch.object(m, "remote_version", return_value=version(state)):
                with self.assertRaises(m.ReleaseError):
                    m.release(api, C, R)
            self.assertFalse(any(x.args[0] == "POST" for x in api.request.call_args_list))

    def test_release_posts_exact_approved_version(self):
        api = Mock()
        api.request.return_value = {"data": {"attributes": {"version": "120"}}}
        with patch.object(m, "remote_version", return_value=version("PENDING_DEVELOPER_RELEASE")):
            m.release(api, C, R)
        call = api.request.call_args_list[-1]
        self.assertEqual(call.args[0:2], ("POST", "/v1/appStoreVersionReleaseRequests"))
        self.assertEqual(call.args[2]["data"]["relationships"]["appStoreVersion"]["data"]["id"], "v1")

    def test_submit_stops_on_remote_content_drift(self):
        api = Mock()
        api.list.return_value = [{"id": "loc1", "attributes": {"locale": "en-US", "whatsNew": "Changed remotely"}}]
        with patch.object(m, "remote_version", return_value=version()), patch.object(m, "fastlane") as run:
            with self.assertRaises(m.ReleaseError):
                m.submit(api, "demo", C, R, {})
            run.assert_not_called()
        api.request.assert_not_called()

    def test_build_lookup_disambiguates_marketing_version(self):
        api = Mock()
        api.list.return_value = [{"id": "b_old", "attributes": {"processingState": "VALID"}},
                                 {"id": "b_new", "attributes": {"processingState": "VALID"}}]
        api.request.side_effect = [{"data": {"attributes": {"version": "1.1.0", "platform": "IOS"}}},
                                   {"data": {"attributes": {"version": "1.2.0", "platform": "IOS"}}}]
        self.assertEqual(m.remote_build(api, C, R)["id"], "b_new")

    def test_processing_build_is_not_submittable(self):
        api = Mock()
        api.list.return_value = [{"id": "b1", "attributes": {"processingState": "PROCESSING"}}]
        api.request.return_value = {"data": {"attributes": {"version": "1.2.0", "platform": "IOS"}}}
        with self.assertRaises(m.ReleaseError):
            m.remote_build(api, C, R)

    def test_ipa_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.ipa"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("Payload/Example.app/Info.plist", plistlib.dumps({
                    "CFBundleIdentifier": C["bundle_id"], "CFBundleShortVersionString": "1.2.0", "CFBundleVersion": "119"}))
            with self.assertRaises(m.ReleaseError):
                m.verify_ipa(path, C, R)

    def test_preview_and_dry_run_need_no_network_or_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "apps" / "demo"
            target.mkdir(parents=True)
            for name in ("app.json", "release.json"):
                (target / name).write_bytes((ROOT / "examples" / "demo" / name).read_bytes())
            with patch.object(m, "ROOT", root), patch.object(m, "ASC") as api, patch.object(
                sys, "argv", ["release.py", "prepare", "demo"]):
                m.main()
                api.return_value.request.assert_not_called()
                api.return_value.list.assert_not_called()
            self.assertTrue((root / ".state/demo/1.2.0/120/preview.html").exists())

    def test_missing_language_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "apps" / "demo"
            target.mkdir(parents=True)
            (target / "app.json").write_bytes((ROOT / "examples/demo/app.json").read_bytes())
            (target / "release.json").write_text(json.dumps(R))
            with patch.object(m, "ROOT", root), self.assertRaises(m.ReleaseError):
                m.load_app("demo")

    def test_api_paginates(self):
        api = m.ASC({})
        with patch.object(api, "request", side_effect=[
            {"data": [1], "links": {"next": m.API + "/v1/apps?cursor=x"}},
            {"data": [2], "links": {"next": None}}]):
            self.assertEqual(api.list("/v1/apps"), [1, 2])

    def test_api_does_not_forward_token_to_other_hosts(self):
        api = m.ASC({})
        with patch.object(api, "bearer") as bearer:
            with self.assertRaises(m.ReleaseError):
                api.request("GET", "https://example.com/v1/apps")
            bearer.assert_not_called()

    def test_jwt_team_and_individual_signatures(self):
        try:
            import jwt
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives import serialization
        except ImportError:
            self.skipTest("安装 requirements.txt 后运行 JWT 签名验证")
        key = ec.generate_private_key(ec.SECP256R1())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key.p8"
            path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
            for issuer in ("issuer", ""):
                api = m.ASC({"key_id": "key", "key_path": str(path), "issuer_id": issuer})
                payload = jwt.decode(api.bearer(), key.public_key(), algorithms=["ES256"], audience="appstoreconnect-v1")
                self.assertEqual(payload.get("iss"), issuer or None)
                self.assertEqual(payload.get("sub"), None if issuer else "user")
                self.assertEqual(payload["exp"] - payload["iat"], 600)


if __name__ == "__main__":
    unittest.main()
