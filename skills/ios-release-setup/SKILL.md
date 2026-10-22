---
name: ios-release-setup
description: 为已安装的 iOS 自动发布插件初始化当前 Mac 的独立运行环境、API 密钥路径和签名检查；也用于插件首次安装后的引导。
---

# 初始化 iOS 自动发布插件

从本文件向上两级定位插件根目录。保持当前 App 聊天/目录；安装运行依赖不需要搬动 App 源码或复制工具到每个 App。

用户安装/设置本插件的指令授权本机普通依赖初始化。运行 `python3 <插件>/scripts/bootstrap.py --auto`，它会检查并复用已有环境、创建独立 Python venv、识别现代 Ruby 和 gem 目录、安装 Bundler/fastlane，并保存个人路径。已有 Homebrew 时自动补装 Ruby 3.3。插件升级不丢失已有 API 配置。

退出码 2 表示依赖仍有待办，读取 `~/.config/ios-app-release/setup-report.json`（或 `IOS_RELEASE_CONFIG_HOME`）并继续处理；不能把这种结果视为环境成功就绪。报告区分依赖、API 连通和具体 App 的签名验证。

- 缺 Python 依赖时，执行 `python3 <插件>/scripts/bootstrap.py --install-python --init-config`，它在个人配置目录创建独立 venv。
- fastlane 使用已有 Ruby 3.1+ 或通过现有 Homebrew 安装 Ruby 3.3，依赖装在个人运行目录，Ruby/gem 路径持久化到个人配置，之后在其他聊天也能使用，不修改系统 Ruby 或 shell 启动文件。缺少 Homebrew 且无现代 Ruby 时依据 https://brew.sh 官方说明协助安装；若涉及管理员密码、Xcode 许可或系统交互，只在该步骤让用户本人操作，完成后继续 --auto。不要运行 sudo gem install。
- 完整 Xcode 已安装但 xcode-select 指向 CommandLineTools 时，插件可通过自己的 DEVELOPER_DIR 使用完整 Xcode；不全局切换机器设置。Xcode 首次启动组件和许可需要由本人完成。
- API 配置模板位于 `~/.config/ios-app-release/machine.json`（或 `IOS_RELEASE_CONFIG_HOME`）。填 Key ID、Issuer ID（Individual key 留空）和本机 `.p8` 路径。先检查用户是否已有可用配置，只问缺失信息。不要读取/显示 `.p8` 内容到聊天或要求发送私钥。
- API 访问不代表签名环境已就绪。检查 Xcode 和项目签名设置；现有证书、账号或描述文件仍需适配本机。不要撤销证书或复制别人的私钥。

API 参数补齐后，使用个人 venv 执行 `scripts/bootstrap.py --verify-api` 做只读连通检查。安装本身不上传或提审 App。初始化完成后，在原有 App 开发聊天调用 `$ios-app-release` 或同插件 `scripts/project.py inspect --project <当前App目录>`。有歧义或缺少凭据时给出准确待办；依赖未安装、签名未实测不能说“已可一键发布”。
