#!/usr/bin/env python3
"""iOS release workspace: local plans, Xcode builds, official ASC API metadata."""
import argparse
import contextlib
import fcntl
import hashlib
import html
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent
ROOT = TOOL_ROOT  # Mutable workspace; the installed plugin itself stays read-only.
CONFIG_HOME = Path(os.environ.get("IOS_RELEASE_CONFIG_HOME", str(Path.home() / ".config/ios-app-release"))).expanduser()
API = "https://api.appstoreconnect.apple.com"
FIELDS = {"whatsNew": 4000, "description": 4000, "keywords": 100,
          "promotionalText": 170, "supportUrl": None, "marketingUrl": None}
EDITABLE = {"PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED", "REJECTED", "METADATA_REJECTED"}


class ReleaseError(Exception):
    pass


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ReleaseError("无法读取 JSON %s：%s" % (path, e)) from e


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def load_app(slug):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        raise ReleaseError("App 别名只允许小写字母、数字和连字符")
    directory = ROOT / "apps" / slug
    c, r = read_json(directory / "app.json"), read_json(directory / "release.json")
    if not isinstance(c, dict) or not isinstance(r, dict):
        raise ReleaseError("app.json 和 release.json 顶层必须为对象")
    for key in ("bundle_id", "app_store_id", "source", "scheme", "team_id", "locales"):
        if not c.get(key):
            raise ReleaseError("app.json 缺少 %s" % key)
    if bool(c.get("project")) == bool(c.get("workspace")):
        raise ReleaseError("project 和 workspace 必须且只能配置一项")
    if not re.fullmatch(r"[A-Za-z0-9.\-]+", c["bundle_id"]):
        raise ReleaseError("bundle_id 格式错误")
    if not re.fullmatch(r"[A-Z0-9]{10}", c["team_id"]):
        raise ReleaseError("team_id 必须为 10 位大写字母或数字")
    if not isinstance(c["app_store_id"], str) or not c["app_store_id"].isdigit():
        raise ReleaseError("app_store_id 必须为数字字符串")
    for key, pattern in (("version", r"\d+\.\d+(?:\.\d+)?"), ("build", r"\d+(?:\.\d+){0,2}")):
        if not isinstance(r.get(key), str) or not re.fullmatch(pattern, r[key]):
            raise ReleaseError("release.json 的 %s 必须为有效的版本字符串" % key)
    locales = c["locales"]
    if not isinstance(locales, list) or not all(isinstance(x, str) for x in locales) or len(set(locales)) != len(locales):
        raise ReleaseError("locales 必须为不重复的语言代码列表")
    if not isinstance(r.get("localizations"), dict) or set(r["localizations"]) != set(locales):
        raise ReleaseError("localizations 必须覆盖 locales 中的每一种语言，且不能多出其他语言")
    for locale, values in r["localizations"].items():
        if not isinstance(values, dict) or not isinstance(values.get("whatsNew"), str) or not values["whatsNew"].strip():
            raise ReleaseError("%s 缺少更新说明 whatsNew" % locale)
        for field, value in values.items():
            if field not in FIELDS or not isinstance(value, str) or not value.strip():
                raise ReleaseError("%s 的 %s 不受支持或为空；省略字段表示保留原值" % (locale, field))
            if FIELDS[field] and len(value) > FIELDS[field]:
                raise ReleaseError("%s 的 %s 超过 %s 字符" % (locale, field, FIELDS[field]))
            if field.endswith("Url") and urllib.parse.urlparse(value).scheme not in ("http", "https"):
                raise ReleaseError("%s 的 %s 必须为 HTTP(S) 地址" % (locale, field))
    # Only these configured fields go to ASC. Unknown release options must not silently change policy.
    if set(r) - {"version", "build", "localizations"}:
        raise ReleaseError("release.json 包含未知字段")
    return c, r


def machine():
    shared = CONFIG_HOME / "machine.json"
    c = read_json(shared) if shared.exists() else {}
    path = ROOT / ".machine.json"
    if path.exists():
        c.update(read_json(path))
    for key, env in (("key_id", "ASC_KEY_ID"), ("issuer_id", "ASC_ISSUER_ID"),
                     ("key_path", "ASC_KEY_PATH"), ("source_root", "IOS_SOURCE_ROOT")):
        if env in os.environ:
            c[key] = os.environ[env]
    return c


class ASC:
    def __init__(self, settings):
        self.settings = settings
        self.token, self.expires = None, 0

    def bearer(self):
        if self.token and time.time() < self.expires - 30:
            return self.token
        try:
            import jwt
        except ImportError as e:
            raise ReleaseError("先安装 Python 依赖：.venv/bin/python -m pip install -r requirements.txt") from e
        for key in ("key_id", "key_path"):
            if not self.settings.get(key):
                raise ReleaseError("请在 .machine.json 或环境变量配置 %s" % key)
        key_path = Path(self.settings["key_path"]).expanduser()
        now = int(time.time())
        payload = {"iat": now, "exp": now + 600, "aud": "appstoreconnect-v1"}
        if self.settings.get("issuer_id"):
            payload["iss"] = self.settings["issuer_id"]
        else:
            payload["sub"] = "user"  # Individual API key; no provisioning endpoint access.
        try:
            self.token = jwt.encode(payload, key_path.read_text(), algorithm="ES256",
                                    headers={"kid": self.settings["key_id"], "typ": "JWT"})
        except Exception as e:
            raise ReleaseError("无法使用 API 私钥签名，请检查 key_path、私钥类型和文件权限") from e
        self.expires = now + 600
        return self.token

    def request(self, method, path, data=None, params=None):
        url = path if path.startswith("https://") else API + path
        if urllib.parse.urlparse(url).netloc != "api.appstoreconnect.apple.com":
            raise ReleaseError("拒绝向非 Apple API 地址发送凭据")
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(url, body, method=method, headers={
            "Authorization": "Bearer " + self.bearer(), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise ReleaseError("Apple API %s：%s\n%s\n请求未自动重试，请先检查远端状态。" % (e.code, path, detail)) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise ReleaseError("Apple API 网络异常；结果可能未知，请先运行 status，再决定是否重试") from e

    def list(self, path, params=None):
        rows = []
        while path:
            response = self.request("GET", path, params=params)
            rows.extend(response["data"])
            path = response.get("links", {}).get("next")
            params = None
        return rows


def resource(kind, attributes=None, relationships=None, identity=None):
    d = {"type": kind}
    if attributes is not None:
        d["attributes"] = attributes
    if relationships:
        d["relationships"] = {k: {"data": {"type": t, "id": i}} for k, (t, i) in relationships.items()}
    if identity:
        d["id"] = identity
    return {"data": d}


def remote_app(api, c):
    rows = api.list("/v1/apps", {"filter[bundleId]": c["bundle_id"], "limit": 200})
    if len(rows) != 1 or rows[0]["id"] != c["app_store_id"]:
        raise ReleaseError("API 账号下的 Bundle ID 与 app_store_id 不匹配，已停止")
    return rows[0]


def remote_version(api, c, r):
    remote_app(api, c)
    rows = api.list("/v1/apps/%s/appStoreVersions" % c["app_store_id"], {
        "filter[platform]": "IOS", "filter[versionString]": r["version"], "limit": 200})
    if len(rows) > 1:
        raise ReleaseError("发现多个相同版本，需人工检查")
    return rows[0] if rows else None


def remote_build(api, c, r):
    rows = api.list("/v1/builds", {"filter[app]": c["app_store_id"],
        "filter[version]": r["build"], "include": "preReleaseVersion", "limit": 200})
    # Read marketing version from the relationship; build numbers may repeat across trains.
    matches = []
    for b in rows:
        train = api.request("GET", "/v1/builds/%s/preReleaseVersion" % b["id"])["data"]
        if train["attributes"]["version"] == r["version"] and train["attributes"]["platform"] == "IOS":
            matches.append(b)
    if len(matches) != 1:
        raise ReleaseError("未找到唯一的 %s (%s) 构建；可能仍在处理，请稍后重试" % (r["version"], r["build"]))
    b = matches[0]
    if b["attributes"].get("processingState") != "VALID" or b["attributes"].get("expired"):
        raise ReleaseError("指定构建尚未处理完成、无效或已过期")
    return b


def build_export_compliance(api, build):
    """Read declaration completeness without inferring or changing encryption use."""
    value = build.get("attributes", {}).get("usesNonExemptEncryption")
    result = {"build_id": build["id"], "usesNonExemptEncryption": value, "ready": False}
    if value is False:
        return dict(result, ready=True, status="EXEMPT",
                    summary="构建已声明不使用非豁免加密；声明真实性仍需按 App 和第三方 SDK 核实")
    if value is not True:
        return dict(result, status="MISSING", summary=(
            "构建缺少出口合规信息：usesNonExemptEncryption 未填写。请在 App Store Connect 的构建版本旁点“管理”完成加密问卷；"
            "已上传构建可直接补填，无需重新上传。仅在核实 App 及第三方 SDK 属于豁免后，才为后续构建配置 "
            "Info.plist 的 ITSAppUsesNonExemptEncryption=NO；插件不会自动猜填。"))
    try:
        declaration = api.request("GET", "/v1/builds/%s/appEncryptionDeclaration" % build["id"])["data"]
    except (ReleaseError, KeyError, TypeError) as e:
        return dict(result, status="UNKNOWN", summary="无法核实构建出口合规文稿，提审前必须重查：%s" % e)
    if not declaration:
        return dict(result, status="DOCUMENT_MISSING", summary=(
            "构建声明使用非豁免加密，但尚未关联出口合规文稿；请在 App Store Connect 上传并关联适用的文稿，等待 Apple 批准。"))
    state = declaration.get("attributes", {}).get("appEncryptionDeclarationState")
    result.update(declaration_id=declaration["id"], declaration_state=state)
    if state == "APPROVED":
        return dict(result, ready=True, status="APPROVED", summary="构建已关联 Apple 批准的出口合规文稿")
    return dict(result, status="DOCUMENT_NOT_APPROVED",
                summary="构建出口合规文稿尚未获批（%s）；请在 App Store Connect 处理后重新检查" % (state or "未知状态"))


def output_dir(slug, r):
    return ROOT / ".state" / slug / r["version"] / r["build"]


def fingerprint(c, r):
    return hashlib.sha256(json.dumps([c, r], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def plan(slug, c, r):
    out = output_dir(slug, r)
    out.mkdir(parents=True, exist_ok=True)
    token = "%s@%s(%s)" % (slug, r["version"], r["build"])
    print("%s | %s | 语言：%s | Apple 审核后手动上线" % (token, c["bundle_id"], ", ".join(c["locales"])))
    sections = []
    for locale, values in r["localizations"].items():
        sections.append("<h2>%s</h2>" % html.escape(locale))
        for key, value in values.items():
            sections.append("<h3>%s</h3><pre>%s</pre>" % (html.escape(key), html.escape(value)))
    page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>发布检查</title>
<style>body{font:16px system-ui;max-width:900px;margin:40px auto;padding:20px}pre{white-space:pre-wrap;background:#f3f5f7;padding:16px}h2{border-top:1px solid #ddd;padding-top:24px}</style>
<h1>%s</h1><p>Bundle ID：%s</p><p>未列出的字段保留原值；沿用已有截图。只预览本地拟上传内容，远端差异请使用 diff。</p>%s</html>""" % (html.escape(token), html.escape(c["bundle_id"]), "".join(sections))
    report = out / "preview.html"
    report.write_text(page, encoding="utf-8")
    print("本地预览：%s" % report)
    return token


def differences(api, version, r):
    rows = api.list("/v1/appStoreVersions/%s/appStoreVersionLocalizations" % version["id"]) if version else []
    locales = {x["attributes"]["locale"]: x for x in rows}
    changes = []
    for locale, values in r["localizations"].items():
        old = locales.get(locale, {}).get("attributes", {})
        for field, value in values.items():
            if old.get(field) != value:
                changes.append({"locale": locale, "field": field, "before": old.get(field), "after": value})
    return locales, changes


def metadata(api, c, r):
    v = remote_version(api, c, r)
    if v and v["attributes"]["appStoreState"] not in EDITABLE:
        raise ReleaseError("版本不可编辑：%s" % v["attributes"]["appStoreState"])
    if v is None:
        v = api.request("POST", "/v1/appStoreVersions", resource("appStoreVersions",
            {"platform": "IOS", "versionString": r["version"], "releaseType": "MANUAL"},
            {"app": ("apps", c["app_store_id"])}))["data"]
    else:
        api.request("PATCH", "/v1/appStoreVersions/" + v["id"], resource("appStoreVersions",
            {"releaseType": "MANUAL"}, identity=v["id"]))
    existing, changes = differences(api, v, r)
    changed_locales = {x["locale"] for x in changes}
    for locale, values in r["localizations"].items():
        if locale not in existing:
            api.request("POST", "/v1/appStoreVersionLocalizations", resource("appStoreVersionLocalizations",
                dict(values, locale=locale), {"appStoreVersion": ("appStoreVersions", v["id"])}))
        elif locale in changed_locales:
            identity = existing[locale]["id"]
            api.request("PATCH", "/v1/appStoreVersionLocalizations/" + identity,
                resource("appStoreVersionLocalizations", values, identity=identity))
    print("版本资料已同步；%s 个字段变更；发布方式为手动。" % len(changes))


def runtime_config(slug, c, r, settings):
    source = Path(c["source"]).expanduser()
    if not source.is_absolute():
        if c.get("source_base") == "project":
            source = ROOT.parent / source
        elif not settings.get("source_root"):
            raise ReleaseError("请在 .machine.json 配置 source_root，或设置 IOS_SOURCE_ROOT")
        else:
            source = Path(settings["source_root"]).expanduser() / source
    project = source / c.get("workspace", c.get("project"))
    if not project.exists():
        raise ReleaseError("找不到 Xcode 工程：%s" % project)
    return dict(c, **{k: r[k] for k in ("version", "build")}, source=str(source.resolve()),
                output=str(output_dir(slug, r)), export_options=c.get("export_options", {"method": "app-store-connect"}))


def runtime_env(settings):
    env = os.environ.copy()
    paths = []
    if settings.get("ruby_bin"):
        paths.append(str(Path(settings["ruby_bin"]).expanduser().parent))
    if settings.get("ruby_gem_bin"):
        paths.append(str(Path(settings["ruby_gem_bin"]).expanduser()))
    if paths:
        env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
    if settings.get("developer_dir"):
        env["DEVELOPER_DIR"] = str(Path(settings["developer_dir"]).expanduser())
    gemfile = Path(settings.get("bundle_gemfile", str(CONFIG_HOME / "runtime/Gemfile"))).expanduser()
    if gemfile.exists():
        env["BUNDLE_GEMFILE"] = str(gemfile)
        env["BUNDLE_PATH"] = str(gemfile.parent / "gems")
    else:
        env["BUNDLE_GEMFILE"] = str(TOOL_ROOT / "Gemfile")
    return env


def fastlane(lane, slug, c, r, settings):
    config = runtime_config(slug, c, r, settings)
    target = output_dir(slug, r) / "runtime.json"
    write_json(target, config)
    env = runtime_env(settings)
    env.update({"IOS_RELEASE_CONFIG": str(target), "FASTLANE_SKIP_UPDATE_CHECK": "1",
                "FASTLANE_OPT_OUT_USAGE": "1"})
    for key in ("key_id", "issuer_id", "key_path"):
        env["ASC_" + key.upper()] = str(settings.get(key, ""))
    env["ASC_KEY_PATH"] = str(Path(env["ASC_KEY_PATH"]).expanduser())
    # Absolute root Gemfile prevents accidentally loading an app's unrelated Fastfile.
    try:
        subprocess.run(["bundle", "exec", "fastlane", "ios", lane], cwd=TOOL_ROOT, env=env, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise ReleaseError("fastlane %s 未完成；检查上方日志。先运行 doctor，网络失败后先查远端状态。" % lane) from e


def verify_ipa(path, c, r):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info.plist", n)]
        if len(names) != 1:
            raise ReleaseError("IPA 中未找到唯一主应用 Info.plist")
        p = plistlib.loads(z.read(names[0]))
    for key, expected in (("CFBundleIdentifier", c["bundle_id"]), ("CFBundleShortVersionString", r["version"]),
                          ("CFBundleVersion", r["build"])):
        if str(p.get(key)) != expected:
            raise ReleaseError("IPA 的 %s=%r，期望 %s。请让工程 Info.plist 使用对应构建变量。" % (key, p.get(key), expected))


def prepare(api, slug, c, r, settings, metadata_only):
    version = remote_version(api, c, r)  # Validate identity and editability before a lengthy build.
    if version and version["attributes"]["appStoreState"] not in EDITABLE:
        raise ReleaseError("当前版本不可编辑，已停止打包")
    receipt = output_dir(slug, r) / "upload.json"
    if not metadata_only:
        if receipt.exists():
            raise ReleaseError("此构建已有本机上传记录。用 --metadata-only 同步文案，或更换 build。")
        # Any existing build with this marketing version blocks a duplicate upload, including processing builds.
        builds = api.list("/v1/builds", {"filter[app]": c["app_store_id"], "filter[version]": r["build"]})
        for b in builds:
            train = api.request("GET", "/v1/builds/%s/preReleaseVersion" % b["id"])["data"]["attributes"]
            if train["version"] == r["version"] and train["platform"] == "IOS":
                raise ReleaseError("Apple 已存在此版本和构建号；使用 --metadata-only，或更换 build。")
        fastlane("archive", slug, c, r, settings)
        verify_ipa(output_dir(slug, r) / "app.ipa", c, r)
        fastlane("upload", slug, c, r, settings)
        write_json(receipt, {"bundle_id": c["bundle_id"], "version": r["version"], "build": r["build"],
                             "uploaded_at": time.time()})
    metadata(api, c, r)
    write_json(output_dir(slug, r) / "prepared.json", {"fingerprint": fingerprint(c, r)})
    print("准备完成。构建仍可能在 Apple 处理；检查预览和 diff 后再执行 submit。")


def submit(api, slug, c, r, settings):
    v = remote_version(api, c, r)
    if not v or v["attributes"]["appStoreState"] not in EDITABLE:
        raise ReleaseError("版本不存在或不在可提审状态")
    if v["attributes"].get("releaseType") != "MANUAL":
        raise ReleaseError("远端发布方式不是 MANUAL，请先 prepare --metadata-only")
    _, changes = differences(api, v, r)
    if changes:
        raise ReleaseError("远端文案与本地不同，请先 diff 和 prepare --metadata-only")
    b = remote_build(api, c, r)
    compliance = build_export_compliance(api, b)
    if not compliance["ready"]:
        raise ReleaseError("出口合规检查未通过，未执行提审：" + compliance["summary"])
    api.request("PATCH", "/v1/appStoreVersions/" + v["id"], resource("appStoreVersions",
        relationships={"build": ("builds", b["id"])}, identity=v["id"]))
    fastlane("submit", slug, c, r, settings)
    print("提审请求已完成；请用 status 核实。Apple 通过后仍等待你手动上线。")


def release(api, c, r):
    v = remote_version(api, c, r)
    if not v or v["attributes"]["appStoreState"] != "PENDING_DEVELOPER_RELEASE":
        raise ReleaseError("只有 PENDING_DEVELOPER_RELEASE 状态可以手动上线")
    b = api.request("GET", "/v1/appStoreVersions/%s/build" % v["id"])["data"]
    if not b or b["attributes"]["version"] != r["build"]:
        raise ReleaseError("审核通过的构建号与本地指定构建不一致，已停止")
    api.request("POST", "/v1/appStoreVersionReleaseRequests", resource("appStoreVersionReleaseRequests",
        relationships={"appStoreVersion": ("appStoreVersions", v["id"])}))
    print("已请求 Apple 发布。请求成功不代表商店已完成分发，请用 status 核实。")


def doctor(settings):
    env = runtime_env(settings)
    print("Python: " + sys.version.split()[0])
    for binary in ("xcodebuild", "ruby", "bundle"):
        print("%s: %s" % (binary, shutil.which(binary, path=env.get("PATH")) or "未安装"))
    try:
        import jwt
        print("PyJWT: " + jwt.__version__)
    except ImportError:
        print("PyJWT: 未安装，请使用 requirements.txt")
    for name in ("source_root", "key_id", "key_path"):
        print("%s: %s" % (name, "已配置" if settings.get(name) else "未配置"))
    print("API 类型: " + ("未配置" if not settings.get("key_id") else
          "Team key" if settings.get("issuer_id") else "Individual key（需已有本机签名）"))
    if settings.get("key_path"):
        print("私钥文件: " + ("存在" if Path(settings["key_path"]).expanduser().is_file() else "不存在"))
    if shutil.which("bundle", path=env.get("PATH")):
        result = subprocess.run(["bundle", "check"], cwd=TOOL_ROOT, env=runtime_env(settings), capture_output=True, text=True)
        print("fastlane 依赖: " + ("就绪" if result.returncode == 0 else "未就绪；请用独立 Ruby 环境执行 bundle install"))
    print("doctor 仅检查本机；证书、描述文件和 API 权限需接入真实 App 验证。")


@contextlib.contextmanager
def app_lock(slug):
    path = ROOT / ".state" / slug / "operation.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise ReleaseError("此 App 在本机已有发布操作运行") from e
        yield


def main():
    global ROOT
    parser = argparse.ArgumentParser(description="iOS 多 App 发布。外部写入必须带 --execute。")
    parser.add_argument("command", choices=["doctor", "list", "plan", "diff", "status", "prepare", "submit", "release"])
    parser.add_argument("apps", nargs="*", help="App 别名；plan/prepare/diff/status 支持多个")
    parser.add_argument("--execute", action="store_true", help="实际上传、提审或上线")
    parser.add_argument("--confirm", help="submit/release 的准确目标：别名@版本(构建号)")
    parser.add_argument("--metadata-only", action="store_true", help="prepare 只同步版本文案，不打包上传")
    parser.add_argument("--workspace", type=Path, help="App 项目的 .ios-release 目录；与插件安装目录分离")
    args = parser.parse_args()
    if args.workspace:
        ROOT = args.workspace.expanduser().resolve()
    settings = machine()
    if args.command == "doctor":
        doctor(settings)
        return
    if args.command == "list":
        for p in sorted((ROOT / "apps").glob("*/app.json")):
            print(p.parent.name)
        return
    if not args.apps:
        parser.error("请指定 App 别名，先按 README 添加 apps/<别名> 配置")
    if args.command in ("submit", "release") and len(args.apps) != 1:
        parser.error("提审和上线每次需要明确一个 App")
    api = ASC(settings)
    for slug in args.apps:
        c, r = load_app(slug)
        token = plan(slug, c, r)
        if args.command == "plan":
            continue
        if args.command in ("prepare", "submit", "release") and not args.execute:
            print("仅预览；尚未进行外部写入。实际执行需 --execute。")
            continue
        if args.command in ("submit", "release") and args.confirm != token:
            raise ReleaseError("目标确认不匹配。检查预览后使用 --confirm '%s'" % token)
        if args.command == "status":
            v = remote_version(api, c, r)
            print(json.dumps(v, ensure_ascii=False, indent=2) if v else "远端尚无此版本")
        elif args.command == "diff":
            v = remote_version(api, c, r)
            _, changes = differences(api, v, r)
            print(json.dumps(changes, ensure_ascii=False, indent=2))
        else:
            with app_lock(slug):
                if args.command == "prepare":
                    prepare(api, slug, c, r, settings, args.metadata_only)
                elif args.command == "submit":
                    submit(api, slug, c, r, settings)
                elif args.command == "release":
                    release(api, c, r)


if __name__ == "__main__":
    try:
        main()
    except (ReleaseError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as e:
        print("停止：%s" % e, file=sys.stderr)
        sys.exit(1)
