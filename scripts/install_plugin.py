#!/usr/bin/env python3
"""One command to register and install this native plugin from private Git/local source."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from bootstrap import locate_codex

ROOT = Path(__file__).resolve().parents[1]
def main():
    parser = argparse.ArgumentParser(description="安装原生插件，并自动检查配置本机运行环境")
    parser.add_argument("--source", default=str(ROOT), help="本地仓库目录或已授权可访问的私有 Git 地址")
    parser.add_argument("--ref", help="可选 Git 分支/标签")
    parser.add_argument("--skip-setup", action="store_true", help="只安装插件，不配置运行环境")
    args = parser.parse_args()
    codex = locate_codex()
    if not codex:
        parser.error("未找到 Codex CLI；请在桌面 app 的终端环境执行或配置 CLI")
    if Path(args.source).expanduser().resolve() == ROOT:
        subprocess.run([sys.executable, str(ROOT / "scripts/package_plugin.py")], check=True)
    command = [codex, "plugin", "marketplace", "add", args.source]
    if args.ref:
        command.extend(["--ref", args.ref])
    subprocess.run(command, check=True)
    result = subprocess.run([codex, "plugin", "add", "ios-app-release@ios-release-tools", "--json"],
                            capture_output=True, text=True, check=True)
    installed = json.loads(result.stdout)
    root = Path(installed["installedPath"])
    manifest = json.loads((root / "plugin.json").read_text())
    if manifest["name"] != "ios-app-release" or not (root / "scripts/bootstrap.py").is_file():
        raise RuntimeError("安装结果不是预期的完整插件，停止初始化")
    print("原生插件已安装：%s" % root, flush=True)
    if not args.skip_setup:
        result = subprocess.run([sys.executable, str(root / "scripts/bootstrap.py"), "--auto"])
        if result.returncode:
            print("插件安装成功，环境仍有待处理事项；请让 Codex 阅读 setup-report.json 并继续处理。")
            return result.returncode
    print("按客户端提示刷新技能列表；在原有 App 开发聊天中使用 $ios-app-release。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, KeyError, ValueError, subprocess.CalledProcessError) as e:
        print("安装未完成：%s" % e, file=sys.stderr)
        sys.exit(1)
