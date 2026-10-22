#!/usr/bin/env python3
"""Build a clean, self-contained native plugin; never ship machine/App data."""
import argparse
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ["plugin.json", ".codex-plugin/plugin.json", "release.py", "preflight.py", "requirements.txt", "Gemfile", "README.md", "INSTALL_FOR_CODEX.md"]
DIRS = ["skills", "fastlane"]
SCRIPTS = ["project.py", "bootstrap.py"]


def package(destination):
    destination.mkdir(parents=True, exist_ok=True)
    allowed = set(FILES + ["scripts/" + x for x in SCRIPTS])
    if (ROOT / "Gemfile.lock").exists():
        allowed.add("Gemfile.lock")
    for name in DIRS:
        allowed.update(str(p.relative_to(ROOT)) for p in (ROOT / name).rglob("*") if p.is_file()
                       and "__pycache__" not in p.parts and p.name not in {"report.xml", "README.md"})
    # Delete only stale files inside this generated package, not its source tree.
    for path in destination.rglob("*"):
        if path.is_file() and str(path.relative_to(destination)) not in allowed:
            path.unlink()
    for name in sorted(allowed):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    return sorted(allowed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成可供私有 marketplace 安装的完整发布插件")
    parser.add_argument("--destination", type=Path, default=ROOT / "plugins/ios-app-release")
    parser.add_argument("--zip", action="store_true")
    args = parser.parse_args()
    paths = package(args.destination)
    print("已打包 %s 个文件：%s" % (len(paths), args.destination))
    if args.zip:
        version = json.loads((ROOT / "plugin.json").read_text())["version"]
        archive = ROOT / "dist" / ("ios-app-release-" + version + ".zip")
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for name in paths:
                z.write(args.destination / name, "ios-app-release/" + name)
        print("ZIP：%s" % archive)
