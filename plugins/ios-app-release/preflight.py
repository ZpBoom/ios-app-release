"""Read-only project/ASC discovery. Reports uncertainty instead of guessing app identity."""
import datetime
import html
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess

import release as core

SKIP = {".git", ".venv", "Pods", "Carthage", ".build", "build", "DerivedData", "node_modules",
        ".ios-release", ".state", "plugins"}
PUBLISHED = {"READY_FOR_SALE", "READY_FOR_DISTRIBUTION", "REPLACED_WITH_NEW_VERSION",
             "DEVELOPER_REMOVED_FROM_SALE", "REMOVED_FROM_SALE"}


def item(report, code, level, summary, evidence=None):
    row = {"code": code, "level": level, "summary": summary}
    if evidence is not None:
        row["evidence"] = evidence
    report["checks"].append(row)


def walk_project(project):
    for base, dirs, files in os.walk(project, followlinks=False):
        relative = Path(base).relative_to(project)
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith(".") and not (Path(base) / d).is_symlink())
        if len(relative.parts) > 6:
            dirs[:] = []
            continue
        yield Path(base), dirs, sorted(files)


def xcode_json(args, project):
    try:
        result = subprocess.run(["xcodebuild"] + args, cwd=project, env=core.runtime_env(core.machine()),
                                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise core.ReleaseError("Xcode 查询失败或超时，请检查 Xcode、Scheme 和依赖解析") from e
    if result.returncode:
        raise core.ReleaseError("Xcode 查询失败：" + result.stderr[-2500:])
    try:
        return json.loads(result.stdout)
    except ValueError as e:
        raise core.ReleaseError("Xcode 没有返回有效 JSON") from e


def local_export_compliance(project, selected, settings, report):
    """Inspect the selected main target, never an arbitrary Info.plist in the tree."""
    value, origin = None, None
    source = settings.get("INFOPLIST_FILE")
    override = settings.get("INFOPLIST_KEY_ITSAppUsesNonExemptEncryption")
    generated_override = settings.get("GENERATE_INFOPLIST_FILE") == "YES" and override is not None
    if source and not generated_override:
        # Xcode normally expands build variables in -showBuildSettings output.
        source = re.sub(r"\$\(([^)]+)\)|\$\{([^}]+)\}",
                        lambda m: settings.get(m[1] or m[2], m[0]), source)
        path = Path(source)
        if not path.is_absolute():
            path = Path(settings.get("SRCROOT") or settings.get("PROJECT_DIR") or project / Path(selected).parent) / path
        origin = str(path)
        try:
            with path.open("rb") as stream:
                plist = plistlib.load(stream)
            if not isinstance(plist, dict):
                raise ValueError("Info.plist 顶层必须为字典")
            value = plist.get("ITSAppUsesNonExemptEncryption")
        except (OSError, ValueError, plistlib.InvalidFileException) as e:
            item(report, "local_export_compliance", "warning", "无法读取主 App 的出口合规配置；上传后仍需核实构建声明",
                 {"source": origin, "error": str(e)})
            return
    if generated_override:
        value = {"yes": True, "no": False, "true": True, "false": False, "1": True, "0": False}.get(str(override).strip().lower())
        origin = "INFOPLIST_KEY_ITSAppUsesNonExemptEncryption"
    evidence = {"source": origin, "ITSAppUsesNonExemptEncryption": value}
    if type(value) is bool:
        item(report, "local_export_compliance", "pass", "主 App 已配置出口合规声明（%s）；需以实际上传构建为准" % (
            "YES，上传后需核实关联文稿" if value else "NO"), evidence)
    else:
        item(report, "local_export_compliance", "warning",
             "主 App 未配置有效的 ITSAppUsesNonExemptEncryption 布尔值；上传后需完成加密问卷。请核实 App 和第三方 SDK 后配置，不自动设为 NO", evidence)


def discover(project, report, selected=None, scheme=None, target=None):
    workspaces, projects, docs, languages = [], [], [], set()
    for base, dirs, files in walk_project(project):
        for name in list(dirs):
            path = base / name
            if name.endswith(".xcodeproj"):
                projects.append(str(path.relative_to(project)))
                dirs.remove(name)  # Ignore the project's internal .xcworkspace.
            elif name.endswith(".xcworkspace"):
                workspaces.append(str(path.relative_to(project)))
                dirs.remove(name)
            elif name.endswith(".lproj"):
                languages.add(name[:-6])
        for name in files:
            path = base / name
            lower = name.lower()
            if lower.endswith((".md", ".txt", ".json")) and (
                    any(k in lower for k in ("readme", "changelog", "release", "privacy", "appstore"))
                    or any(k in str(path.relative_to(project)).lower() for k in ("metadata", "docs/"))):
                docs.append(str(path.relative_to(project)))
            if name.endswith(".xcstrings"):
                try:
                    strings = json.loads(path.read_text(encoding="utf-8"))
                    languages.add(strings.get("sourceLanguage", ""))
                    for value in strings.get("strings", {}).values():
                        languages.update(value.get("localizations", {}))
                except (ValueError, OSError):
                    item(report, "string_catalog", "blocker", "无法解析字符串目录", str(path.relative_to(project)))
    report["materials"] = docs[:200]
    report["project_languages"] = sorted(languages - {"", "Base"})
    report["xcode_candidates"] = {"workspaces": workspaces, "projects": projects}
    candidates = workspaces or projects
    if selected:
        path = Path(selected)
        path = path if path.is_absolute() else project / path
        if not path.is_dir() or not path.name.endswith((".xcodeproj", ".xcworkspace")) or not path.resolve().is_relative_to(project):
            item(report, "xcode_selection", "blocker", "指定工程必须位于当前 App 项目中")
            return None
        selected = str(path.relative_to(project))
    elif len(candidates) == 1:
        selected = candidates[0]
    else:
        item(report, "xcode_selection", "blocker", "没有找到唯一 Xcode 工程，需要指定 --xcode", candidates)
        return None
    flag = "-workspace" if selected.endswith(".xcworkspace") else "-project"
    listing = xcode_json([flag, selected, "-list", "-json"], project)
    schemes = listing.get("workspace", listing.get("project", {})).get("schemes", [])
    if scheme is None:
        if len(schemes) == 1:
            scheme = schemes[0]
        else:
            item(report, "scheme_selection", "blocker", "需要指定发布 Scheme，不能自动选择第一个", schemes)
            return None
    elif scheme not in schemes:
        item(report, "scheme_selection", "blocker", "指定 Scheme 未出现在 Xcode 列表中", schemes)
        return None
    settings = xcode_json([flag, selected, "-scheme", scheme, "-configuration", "Release",
                           "-destination", "generic/platform=iOS", "-showBuildSettings", "-json"], project)
    apps = []
    for row in settings:
        s = row.get("buildSettings", {})
        ios = "iphoneos" in s.get("SUPPORTED_PLATFORMS", "") or s.get("PLATFORM_NAME") == "iphoneos"
        main = s.get("PRODUCT_TYPE") == "com.apple.product-type.application" or s.get("WRAPPER_EXTENSION") == "app"
        if ios and main and (not target or row.get("target") == target):
            apps.append(row)
    if len(apps) != 1:
        item(report, "target_selection", "blocker", "未找到唯一 iOS 主 App Target，需要检查 Scheme 或指定 --target",
             [x.get("target") for x in apps])
        return None
    row, s = apps[0], apps[0]["buildSettings"]
    result = {"xcode": selected, "scheme": scheme, "target": row.get("target"),
              "bundle_id": s.get("PRODUCT_BUNDLE_IDENTIFIER"), "team_id": s.get("DEVELOPMENT_TEAM"),
              "version": s.get("MARKETING_VERSION"), "build": s.get("CURRENT_PROJECT_VERSION"),
              "signing_style": s.get("CODE_SIGN_STYLE"), "display_name": s.get("PRODUCT_NAME")}
    for key in ("bundle_id", "team_id", "version", "build"):
        value = result.get(key)
        item(report, "project_" + key, "pass" if value and "$(" not in value else "blocker",
             "工程 %s：%s" % (key, value or "未解析"))
    report["identity"] = result
    item(report, "xcode_discovery", "pass", "已识别 Release 配置的 iOS 主 App", selected)
    local_export_compliance(project, selected, s, report)
    return result


def version_key(value):
    if not re.fullmatch(r"\d+(?:\.\d+){0,2}", str(value)):
        raise core.ReleaseError("版本号无法比较：%s" % value)
    numbers = tuple(int(x) for x in value.split("."))
    return numbers + (0,) * (3 - len(numbers))


def state(v):
    a = v.get("attributes", {})
    return a.get("appVersionState") or a.get("appStoreState")


def classify(versions, desired):
    published = [v for v in versions if state(v) in PUBLISHED]
    latest = max(published, key=lambda v: version_key(v["attributes"]["versionString"])) if published else None
    targets = [v for v in versions if v["attributes"].get("versionString") == desired]
    if len(targets) > 1:
        raise core.ReleaseError("同一 iOS 版本出现多条记录，需要人工检查")
    current = targets[0] if targets else None
    action = "CREATE_VERSION"
    if current:
        action = "REUSE_DRAFT" if state(current) in core.EDITABLE else "BLOCKED_STATE"
        if state(current) in PUBLISHED:
            action = "ALREADY_RELEASED"
        elif state(current) == "PENDING_DEVELOPER_RELEASE":
            action = "READY_TO_RELEASE"
    elif latest and version_key(desired) <= version_key(latest["attributes"]["versionString"]):
        action = "VERSION_NOT_NEWER"
    # Another active draft/review prevents creating an unrelated new draft.
    active = [v for v in versions if state(v) not in PUBLISHED and state(v) not in {
        "INVALID_BINARY", "REPLACED_WITH_NEW_VERSION"}]
    if not current and active:
        action = "OTHER_VERSION_IN_PROGRESS"
    return {"release_kind": "UPDATE" if published else "FIRST_RELEASE", "version_action": action,
            "latest_published": latest, "target_version": current, "active_versions": active}


def remote(api, identity, desired, build, report, expected_id=None):
    apps = api.list("/v1/apps", {"filter[bundleId]": identity["bundle_id"], "limit": 200})
    if not apps:
        report["online"] = {"release_kind": "UNKNOWN", "version_action": "APP_RECORD_NOT_VISIBLE"}
        item(report, "app_record", "blocker", "当前凭据看不到此 App：需确认账号权限或首次创建 App 记录；尚不能断言是新 App")
        return
    if len(apps) != 1 or (expected_id and apps[0]["id"] != expected_id):
        item(report, "app_record", "blocker", "远端 App 身份不唯一或与保存的 App ID 不同，停止自动匹配")
        return
    app = apps[0]
    versions = api.list("/v1/apps/%s/appStoreVersions" % app["id"], {"filter[platform]": "IOS", "limit": 200})
    online = classify(versions, desired)
    online.update({"app_store_id": app["id"], "app_name": app["attributes"].get("name"),
                   "primary_locale": app["attributes"].get("primaryLocale")})
    report["online"] = online
    item(report, "app_record", "pass", "已匹配 Bundle ID 与 Apple App 记录", app["id"])
    action = online["version_action"]
    item(report, "release_route", "pass" if action in {"CREATE_VERSION", "REUSE_DRAFT"} else "blocker",
         ("版本更新" if online["release_kind"] == "UPDATE" else "首次上架（已有 App 记录）") + "；" + action)
    # For new versions only the latest published version is an inheritance source.
    source = online["target_version"] or online["latest_published"]
    locales = api.list("/v1/appStoreVersions/%s/appStoreVersionLocalizations" % source["id"]) if source else []
    online["localizations"] = {v["attributes"]["locale"]: v["attributes"] for v in locales}
    builds = api.list("/v1/builds", {"filter[app]": app["id"], "filter[version]": build,
        "filter[preReleaseVersion.version]": desired, "filter[preReleaseVersion.platform]": "IOS", "limit": 200})
    online["matching_builds"] = [{"id": b["id"], "state": b["attributes"].get("processingState"),
                                  "expired": b["attributes"].get("expired", False),
                                  "usesNonExemptEncryption": b["attributes"].get("usesNonExemptEncryption")} for b in builds]
    if builds:
        invalid = len(builds) != 1 or any(b["attributes"].get("expired") or
            b["attributes"].get("processingState") in {"INVALID", "FAILED"} for b in builds)
        item(report, "existing_build", "blocker" if invalid else "warning",
             "同号构建无效、过期或不唯一，需处理" if invalid else "此版本/构建号已经存在，沿用远端构建，避免重复上传",
             online["matching_builds"])
        if not invalid and builds[0]["attributes"].get("processingState") == "VALID":
            compliance = core.build_export_compliance(api, builds[0])
            online["matching_builds"][0]["export_compliance"] = compliance
            # Missing compliance stops submission, not archival or metadata recovery.
            item(report, "build_export_compliance", "pass" if compliance["ready"] else "warning",
                 compliance["summary"], compliance)
            if not compliance["ready"]:
                report.setdefault("submission_blockers", []).append(compliance["summary"])
    else:
        item(report, "existing_build", "pass", "尚无此版本/构建号，需要归档上传")


def report_project(project, *, selected=None, scheme=None, target=None, offline=False, api=None, config=None, release=None):
    report = {"schema_version": 1, "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "project": str(project), "checks": [], "online": {"release_kind": "UNKNOWN", "version_action": "NOT_CHECKED"},
              "submission_blockers": [],
              "coverage": {"automated": ["Xcode 工程身份", "版本与构建号", "App 线上版本路由", "版本文案完整性", "主 App 出口合规配置及有效远端构建的声明状态"],
                           "not_verified": ["实际功能与回归测试", "证书和描述文件是否能成功导出", "截图内容与尺寸", "隐私和合规声明真实性", "协议/税务/销售地区", "审核账号与内购资料"]}}
    if config:
        selected = selected or config.get("workspace", config.get("project"))
        scheme = scheme or config.get("scheme")
        target = target or config.get("target")
    try:
        identity = discover(project, report, selected, scheme, target)
    except core.ReleaseError as e:
        item(report, "xcode_query", "blocker", str(e))
        identity = None
    if identity and config:
        for field in ("bundle_id", "team_id"):
            if config.get(field) != identity.get(field):
                item(report, "identity_drift", "blocker", "工程 %s 与已保存配置不一致" % field)
    desired = (release or {}).get("version") or (identity or {}).get("version")
    build = (release or {}).get("build") or (identity or {}).get("build")
    report["desired"] = {"version": desired, "build": build}
    if identity and identity.get("bundle_id") and desired and build:
        if offline:
            item(report, "apple_query", "blocker", "离线模式：未查询 Apple，无法判断首次上架还是更新")
        else:
            try:
                settings = core.machine()
                if api is None and not (settings.get("key_id") and settings.get("key_path")):
                    raise core.ReleaseError("尚未配置 API 凭据：未验证线上记录与版本")
                remote(api or core.ASC(settings), identity, desired, build, report,
                       expected_id=(config or {}).get("app_store_id"))
            except (core.ReleaseError, KeyError, ValueError) as e:
                item(report, "apple_query", "blocker", "线上查询未完成：%s" % e)
                report["online"] = {"release_kind": "UNKNOWN", "version_action": "QUERY_FAILED"}
    if release and config:
        online = report["online"].get("localizations", {})
        for locale in config.get("locales", []):
            values = dict(online.get(locale, {}), **release.get("localizations", {}).get(locale, {}))
            for field in ("description", "supportUrl", "whatsNew"):
                # What's New is not required by Apple on a first release, but we keep a human-readable draft.
                if not values.get(field):
                    item(report, "metadata_" + locale + "_" + field, "blocker", "%s 缺少 %s" % (locale, field))
    else:
        item(report, "project_binding", "blocker", "项目尚未接入，先生成项目发布配置和本次更新文案")
    item(report, "review_scope", "warning", "静态和 API 检查不能替代功能测试及 Apple 审核；未验证项目已列入 coverage")
    report["ready_for_prepare"] = not any(c["level"] == "blocker" for c in report["checks"])
    return report


def save_report(project, report):
    directory = project / ".ios-release/reports"
    core.write_json(directory / "preflight.json", report)
    rows = []
    for check in report["checks"]:
        detail = json.dumps(check.get("evidence"), ensure_ascii=False, indent=2) if "evidence" in check else ""
        rows.append("<li class='%s'><strong>%s</strong> %s<pre>%s</pre></li>" % (
            html.escape(check["level"]), html.escape(check["level"].upper()), html.escape(check["summary"]), html.escape(detail)))
    body = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>iOS 发布检查</title>
<style>body{font:16px system-ui;max-width:1000px;margin:32px auto;padding:20px}li{padding:12px;margin:8px 0;border-left:4px solid #889}pre{white-space:pre-wrap}.blocker{border-color:#c33}.pass{border-color:#298}.warning{border-color:#d90}</style>
<h1>iOS 发布检查</h1><p>%s</p><p>发布类型：%s；版本动作：%s</p><p>可准备上传：%s。此结果不代表可直接提审或已通过功能测试。</p><ul>%s</ul><h2>出口合规提审阻塞项</h2><pre>%s</pre><h2>待独立验证</h2><pre>%s</pre></html>""" % (
        html.escape(report["project"]), html.escape(report["online"]["release_kind"]),
        html.escape(report["online"]["version_action"]), "是" if report["ready_for_prepare"] else "否",
        "".join(rows), html.escape("\n".join(report.get("submission_blockers", [])) or "未发现已检查构建的出口合规阻塞；不代表其他提审条件已满足"),
        html.escape("\n".join(report["coverage"]["not_verified"])))
    (directory / "preflight.html").write_text(body, encoding="utf-8")
    print("检查结果：%s；类型：%s；动作：%s" % (
        "可准备上传" if report["ready_for_prepare"] else "有待处理事项", report["online"]["release_kind"], report["online"]["version_action"]))
    for check in report["checks"]:
        print("[%s] %s" % (check["level"], check["summary"]))
    print("检查报告：%s" % (directory / "preflight.html"))
    return directory / "preflight.html"
