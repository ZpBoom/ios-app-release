#!/usr/bin/env python3
"""Idempotent user-local setup, dependency repair and an honest final setup report."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import venv

PLUGIN = Path(__file__).resolve().parents[1]
CONFIG = Path(os.environ.get("IOS_RELEASE_CONFIG_HOME", str(Path.home() / ".config/ios-app-release"))).expanduser()


def capture(command, env=None):
    try:
        r = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
        return r.returncode == 0, (r.stdout or r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def load_settings():
    path = CONFIG / "machine.json"
    settings = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(settings, dict):
        raise RuntimeError("machine.json 顶层必须是对象")
    return settings


def save_settings(settings):
    CONFIG.mkdir(parents=True, exist_ok=True)
    temp = CONFIG / "machine.json.tmp"
    with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    temp.chmod(0o600)
    temp.replace(CONFIG / "machine.json")


def locate_codex():
    candidate = shutil.which("codex")
    if candidate:
        return candidate
    for name in ("ChatGPT", "Codex"):
        base = Path("/Applications") / (name + ".app/Contents/Resources")
        for suffix in ("codex-cli/CodexCLI.app/Contents/MacOS/codex", "codex"):
            path = base / suffix
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
    return None


def locate_brew():
    candidates = [shutil.which("brew"), "/opt/homebrew/bin/brew", "/usr/local/bin/brew"]
    return next((str(p) for p in candidates if p and Path(p).is_file() and os.access(p, os.X_OK)), None)


def modern_ruby(settings):
    candidates = [settings.get("ruby_bin"), shutil.which("ruby"),
                  "/opt/homebrew/opt/ruby@3.3/bin/ruby", "/usr/local/opt/ruby@3.3/bin/ruby",
                  "/opt/homebrew/opt/ruby/bin/ruby", "/usr/local/opt/ruby/bin/ruby"]
    for path in dict.fromkeys(x for x in candidates if x):
        ok, version = capture([str(Path(path).expanduser()), "--version"])
        match = re.search(r"ruby (\d+)\.(\d+)", version)
        if ok and match and tuple(map(int, match.groups())) >= (3, 1):
            return str(Path(path).expanduser())
    return None


def choose_xcode(settings):
    if settings.get("developer_dir"):
        return
    ok, current = capture(["xcode-select", "-p"])
    full = Path("/Applications/Xcode.app/Contents/Developer")
    if ok and "CommandLineTools" in current and full.is_dir():
        # Select only for this plugin; don't change the system xcode-select setting or request sudo.
        settings["developer_dir"] = str(full)


def environment(settings):
    env = os.environ.copy()
    paths = [str(Path(settings["ruby_bin"]).parent)] if settings.get("ruby_bin") else []
    if settings.get("ruby_gem_bin"):
        paths.append(settings["ruby_gem_bin"])
    env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
    if settings.get("developer_dir"):
        env["DEVELOPER_DIR"] = settings["developer_dir"]
    env.update({"BUNDLE_GEMFILE": str(CONFIG / "runtime/Gemfile"), "BUNDLE_PATH": str(CONFIG / "runtime/gems"),
                "FASTLANE_SKIP_UPDATE_CHECK": "1", "FASTLANE_OPT_OUT_USAGE": "1"})
    return env


def install_fastlane(settings, automatic=False):
    ruby = modern_ruby(settings)
    if not ruby and automatic:
        brew = locate_brew()
        if not brew:
            raise RuntimeError("缺少现代 Ruby 和 Homebrew。请由 Codex 按 https://brew.sh 的官方说明协助安装 Homebrew；需要管理员密码或交互时由本人操作，再重跑 --auto。")
        # A supported Ruby 3 branch avoids an untested jump to the newest Ruby major.
        subprocess.run([brew, "install", "ruby@3.3"], check=True)
        ok, prefix = capture([brew, "--prefix", "ruby@3.3"])
        if ok:
            settings["ruby_bin"] = str(Path(prefix) / "bin/ruby")
        ruby = modern_ruby(settings)
    if not ruby:
        raise RuntimeError("缺少 Ruby 3.1+。使用 --auto 通过已有 Homebrew 安装，或先配置自己的现代 Ruby。")
    settings["ruby_bin"] = ruby
    ok, gem_bin = capture([ruby, "-e", "print Gem.bindir"])
    if not ok:
        raise RuntimeError("无法识别所选 Ruby 的 gem 可执行目录")
    settings["ruby_gem_bin"] = gem_bin
    env = environment(settings)
    bundler, _ = capture([ruby, "-S", "bundle", "--version"], env)
    if not bundler:
        subprocess.run([ruby, "-S", "gem", "install", "bundler", "--no-document"], env=env, check=True)
    runtime = CONFIG / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PLUGIN / "Gemfile", runtime / "Gemfile")
    if (PLUGIN / "Gemfile.lock").exists():
        shutil.copy2(PLUGIN / "Gemfile.lock", runtime / "Gemfile.lock")
    subprocess.run([ruby, "-S", "bundle", "install"], cwd=runtime, env=env, check=True)


def setup_report(settings, verify_api=False):
    env = environment(settings)
    interpreter = CONFIG / "runtime/venv/bin/python"
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "status": "ready" if ok else "needs_action", "detail": detail})

    add("platform", sys.platform == "darwin", "iOS 归档需要 macOS")
    ok, detail = capture(["git", "--version"], env)
    add("git", ok, detail)
    codex = locate_codex()
    ok, detail = capture([codex, "--version"], env) if codex else (False, "未找到 Codex CLI；在桌面 app 中配置 CLI")
    add("codex", ok, detail)
    ok, detail = capture(["xcodebuild", "-version"], env)
    add("xcode", ok and "Xcode" in detail, detail if ok else "完整 Xcode、首次组件或许可尚未就绪；请打开 Xcode 完成本人操作")
    ok, detail = capture([str(interpreter), "-c", "import jwt, cryptography; print('Python dependencies ready')"], env) if interpreter.exists() else (False, "尚未创建独立 Python 环境")
    add("python", ok, detail)
    ruby = modern_ruby(settings)
    add("ruby", bool(ruby), ruby or "需要现代 Ruby")
    if ruby and (CONFIG / "runtime/Gemfile").exists():
        ok, _ = capture([ruby, "-S", "bundle", "check"], env)
    else:
        ok = False
    add("fastlane", ok, "依赖就绪" if ok else "尚未成功安装 fastlane 依赖")
    credentials = dict(settings)
    for key in ("key_id", "issuer_id", "key_path"):
        if "ASC_" + key.upper() in os.environ:
            credentials[key] = os.environ["ASC_" + key.upper()]
    configured = bool(credentials.get("key_id") and credentials.get("key_path") and Path(credentials["key_path"]).expanduser().is_file())
    api_status = "not_tested"
    if configured and verify_api:
        try:
            sys.path.insert(0, str(PLUGIN))
            import release
            release.ASC(credentials).request("GET", "/v1/apps", params={"limit": 1})
            api_status = "connected"
        except Exception:
            api_status = "failed"
    elif not configured:
        api_status = "needs_credentials"
    dependencies_ready = all(c["status"] == "ready" for c in checks)
    return {"plugin_root": str(PLUGIN), "dependencies_ready": dependencies_ready, "checks": checks,
            "api": {"status": api_status, "detail": "API 连通后，仍需在具体 App 中核实上传/提审/发布权限"},
            "signing": {"status": "needs_app_validation", "detail": "签名证书、描述文件和真实归档需在目标 App 验证；安装不等于可发布"}}


def main():
    parser = argparse.ArgumentParser(description="检查并初始化当前 Mac 的 iOS 发布插件环境")
    parser.add_argument("--auto", action="store_true", help="检查、自动安装可用依赖并生成完整报告")
    parser.add_argument("--install-python", action="store_true")
    parser.add_argument("--install-fastlane", action="store_true")
    parser.add_argument("--init-config", action="store_true")
    parser.add_argument("--verify-api", action="store_true", help="只读验证已配置的 API 凭据")
    args = parser.parse_args()
    if sys.version_info < (3, 9):
        raise RuntimeError("需要 Python 3.9+，请先安装可用 Python 后重新运行")
    CONFIG.mkdir(parents=True, exist_ok=True)
    (CONFIG / "installation.json").write_text(json.dumps({"plugin_root": str(PLUGIN)}, indent=2), encoding="utf-8")
    settings = load_settings()
    if args.init_config or args.auto:
        for key in ("key_id", "issuer_id", "key_path"):
            settings.setdefault(key, "")
    choose_xcode(settings)
    runtime = CONFIG / "runtime"
    errors = []
    if args.install_python or args.auto:
        try:
            interpreter = runtime / "venv/bin/python"
            if not interpreter.exists():
                venv.create(runtime / "venv", with_pip=True)
            subprocess.run([str(interpreter), "-m", "pip", "install", "-r", str(PLUGIN / "requirements.txt")], check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            errors.append("Python 依赖安装未完成：%s" % e)
    if args.install_fastlane or args.auto:
        try:
            install_fastlane(settings, automatic=args.auto)
        except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
            errors.append(str(e))
    if args.auto or args.init_config or args.install_fastlane or args.install_python:
        save_settings(settings)
    report = setup_report(settings, verify_api=args.verify_api or args.auto)
    report["installation_errors"] = errors
    (CONFIG / "setup-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for check in report["checks"]:
        print("[%s] %s：%s" % (check["status"], check["name"], check["detail"]))
    for error in errors:
        print("[needs_action] " + error)
    print("API：%s；签名：%s" % (report["api"]["status"], report["signing"]["status"]))
    print("个人配置：%s" % (CONFIG / "machine.json"))
    print("环境报告：%s" % (CONFIG / "setup-report.json"))
    print("插件 Python：%s" % (runtime / "venv/bin/python"))
    print("插件工具：%s" % PLUGIN)
    if args.auto or args.install_fastlane or args.install_python:
        return 0 if report["dependencies_ready"] and not errors else 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as e:
        print("初始化未完成：%s" % e, file=sys.stderr)
        sys.exit(1)
