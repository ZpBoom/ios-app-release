# 给同事复制到 Codex 的安装指令

仓库已公开，读取和安装无需 GitHub 协作者邀请。原仓库仅由 ZpBoom 维护。将下面整段文字复制到 Codex，可在已有 iOS App 开发聊天中执行。

```text
请帮我安装这个公开 GitHub 仓库中的 iOS 自动发布 Codex 插件，并完成本机环境检查和配置：
https://github.com/ZpBoom/ios-app-release

请实际执行安装和验证，不要只给我操作步骤：
1. 检查这台 Mac 的 Git 和 Codex CLI。通过原生 marketplace 安装公开插件：
   codex plugin marketplace add https://github.com/ZpBoom/ios-app-release.git
   codex plugin add ios-app-release@ios-release-tools --json
   如果已安装，核实来源并更新，保留我的配置和其他插件。安装不需要此仓库的写权限，不要申请或授予写权限。
2. 从安装结果的 installedPath 定位完整插件，阅读其中的 ios-release-setup 技能，执行：
   python3 <installedPath>/scripts/bootstrap.py --auto
3. 检查 Git、完整 Xcode、Python 独立环境、现代 Ruby、Bundler、fastlane。复用已有环境，自动补装缺失依赖并配置插件自己的可执行路径。若缺 Homebrew，按其官方说明协助安装；需要管理员密码、Xcode 许可、Apple 登录或系统交互时，只在该步骤让我本人完成，随后继续初始化。不要修改系统 Ruby，不执行 sudo gem install。
4. 配置我本人的 App Store Connect API Key。只询问缺少的 Key ID、Issuer ID（Individual key 留空）和本机 .p8 路径，不要求我把私钥内容发到聊天或提交到仓库。保留已有正确配置，用只读请求验证 API 连通性。
5. 阅读 setup-report.json，区分“已安装”“环境依赖已就绪”“API 已连通”“签名尚需具体 App 验证”。修复能自动处理的问题，准确列出仍需我完成的事项，不把跳过项报告成通过。
6. 如果当前聊天有 iOS 工程，使用插件识别工程并生成发布检查报告，判断首次上架、版本更新、已有草稿或当前账号无权看到记录。安装期间不要上传、提审或发布 App。最终告诉我安装版本、环境检查结果，以及如何在这个开发聊天里使用 $ios-app-release。
```

插件会在个人目录保存 API 配置与独立运行环境，在各 App 项目保存自己的 `.ios-release` 配置。无需把工具复制进每个 App。
