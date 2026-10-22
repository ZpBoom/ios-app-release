#!/usr/bin/env python3
"""Run the installed release plugin from the App's existing Codex conversation."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
import release as core
import preflight


def configs(project):
    directory = project / ".ios-release/apps/app"
    if not (directory / "app.json").exists():
        return None, None
    return core.read_json(directory / "app.json"), core.read_json(directory / "release.json")


def bind(project, report, locales=None):
    directory = project / ".ios-release/apps/app"
    if (directory / "app.json").exists() or (directory / "release.json").exists():
        raise core.ReleaseError("项目已有发布配置，保留原文件；直接检查并编辑本次 release.json 即可")
    identity, online = report.get("identity", {}), report["online"]
    if not identity.get("bundle_id") or not online.get("app_store_id"):
        raise core.ReleaseError("尚未识别唯一主 App 或线上 App 记录；先解决检查报告中的身份问题")
    language_list = locales or sorted(online.get("localizations", {})) or [online.get("primary_locale")]
    if not all(language_list):
        raise core.ReleaseError("无法确定商店语言，请提供 --locales")
    app = {"bundle_id": identity["bundle_id"], "app_store_id": online["app_store_id"],
           "source": ".", "source_base": "project", "scheme": identity["scheme"], "target": identity["target"],
           "team_id": identity["team_id"], "locales": language_list,
           "export_options": {"method": "app-store-connect", "signingStyle": identity.get("signing_style", "Automatic").lower()}}
    app["workspace" if identity["xcode"].endswith(".xcworkspace") else "project"] = identity["xcode"]
    localizations = {}
    for locale in language_list:
        old = online.get("localizations", {}).get(locale, {})
        # Existing descriptions can be retained. Never silently reuse old What's New as this release's notes.
        localizations[locale] = {k: v for k, v in old.items() if k in core.FIELDS and k != "whatsNew" and v}
        localizations[locale]["whatsNew"] = ""
    release = dict(report["desired"], localizations=localizations)
    core.write_json(directory / "app.json", app)
    core.write_json(directory / "release.json", release)
    ignore = project / ".gitignore"
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    entries = [".ios-release/.state/", ".ios-release/reports/", ".ios-release/.machine.json"]
    missing = [x for x in entries if x not in existing.splitlines()]
    if missing:
        ignore.write_text(existing.rstrip() + "\n\n# iOS release local artifacts\n" + "\n".join(missing) + "\n", encoding="utf-8")
    print("项目已接入：%s" % directory)
    print("请根据当前聊天和实际改动填写各语言 whatsNew，再执行 check。不会代填旧版本更新说明。")


def main():
    parser = argparse.ArgumentParser(description="在当前 App 项目中使用 iOS 发布插件")
    parser.add_argument("command", choices=["inspect", "init", "check", "doctor", "plan", "diff", "status", "prepare", "submit", "release"])
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="当前 App 源码根目录，默认当前目录")
    parser.add_argument("--xcode", help="需要时指定 .xcodeproj / .xcworkspace")
    parser.add_argument("--scheme")
    parser.add_argument("--target")
    parser.add_argument("--version", help="init 的目标版本；不会修改工程")
    parser.add_argument("--build", help="init 的目标构建号；不会修改工程")
    parser.add_argument("--locales", help="init 的商店语言，逗号分隔；默认沿用远端语言")
    parser.add_argument("--offline", action="store_true", help="仅检查本地，不判断线上发布类型")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    if not project.is_dir():
        raise core.ReleaseError("项目目录不存在")
    if (args.version or args.build) and args.command != "init":
        parser.error("--version / --build 仅用于首次 init；已接入项目请编辑 release.json")
    if args.command == "doctor":
        core.ROOT = project / ".ios-release"
        core.doctor(core.machine())
        return 0
    core.ROOT = project / ".ios-release"
    app, release = configs(project)
    if args.command in ("inspect", "init", "check") or (args.command == "prepare" and args.execute):
        desired = release
        if args.command == "init" and (args.version or args.build):
            desired = {"version": args.version, "build": args.build}
        report = preflight.report_project(project, selected=args.xcode, scheme=args.scheme, target=args.target,
            offline=args.offline, config=app, release=desired)
        # Syntax/length/language checks are independent of the API discovery result.
        if app:
            try:
                core.load_app("app")
                preflight.item(report, "config_validation", "pass", "发布配置和多语言文本格式有效")
            except (core.ReleaseError, TypeError, KeyError, ValueError) as e:
                preflight.item(report, "config_validation", "blocker", str(e))
        report["ready_for_prepare"] = not any(c["level"] == "blocker" for c in report["checks"])
        preflight.save_report(project, report)
        if args.command == "init":
            bind(project, report, args.locales.split(",") if args.locales else None)
            return 0
        if args.command in ("check", "inspect"):
            return 0 if report["ready_for_prepare"] else 2
        if not report["ready_for_prepare"]:
            raise core.ReleaseError("发布检查尚有阻塞项，未执行打包上传")
        existing = report["online"].get("matching_builds", [])
        if existing and not args.metadata_only:
            if len(existing) != 1 or existing[0]["expired"] or existing[0]["state"] == "FAILED" or existing[0]["state"] == "INVALID":
                raise core.ReleaseError("远端同号构建不唯一、无效或过期；请检查并更换构建号")
            args.metadata_only = True
            print("自动识别到已上传构建：仅同步资料。构建处理完成后才能提审。")
    if args.command not in ("inspect", "init", "check"):
        if not app:
            raise core.ReleaseError("当前项目尚未接入，请先 inspect / init")
        command = [sys.executable, str(PLUGIN_ROOT / "release.py"), args.command, "app", "--workspace", str(core.ROOT)]
        if args.execute:
            command.append("--execute")
        if args.metadata_only:
            command.append("--metadata-only")
        if args.confirm:
            command.extend(["--confirm", args.confirm])
        return subprocess.run(command, cwd=project).returncode
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (core.ReleaseError, OSError, ValueError, KeyError, TypeError) as e:
        print("停止：%s" % e, file=sys.stderr)
        sys.exit(1)
