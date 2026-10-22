#!/usr/bin/env python3
"""Compatibility entry: install the complete native plugin, not an incomplete Skill."""
from pathlib import Path
import subprocess
import sys

print("新版会安装完整的 Codex 插件，包含 Skill 和全部执行工具。", flush=True)
arguments = [x for x in sys.argv[1:] if x != "--replace"]
sys.exit(subprocess.run([sys.executable, str(Path(__file__).with_name("install_plugin.py"))] + arguments).returncode)
