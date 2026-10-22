# iOS 自动发布 Codex 插件

安装一次，在原有 App 开发聊天里说“检查这个 App，准备发布”。插件识别当前原生 Xcode 工程，结合聊天和项目资料整理版本内容，查询 App Store Connect 判断首次上架还是更新，生成检查报告，再按你的指令上传、提审和上线。

插件包含 Skill、检查器、发布脚本和 fastlane 配置。App 源码留在原项目，不必复制工具或切换到专门的发布聊天。

当前版本为 0.3.2，新增出口合规配置检查和提审阻断。归档上传及草稿准备已在真实项目验证；提交审核和正式上线尚未完成端到端实测。

公开仓库：[ZpBoom/ios-app-release](https://github.com/ZpBoom/ios-app-release)。任何人均可读取和安装，原仓库仅由 ZpBoom 账号维护，不向其他账号授予写权限。给同事复制的完整安装提示词见 [INSTALL_FOR_CODEX.md](INSTALL_FOR_CODEX.md)。

## Git 分发与安装

更新插件代码后，提交前执行：

```sh
python3 scripts/package_plugin.py
```

提交源码、`.agents/plugins/marketplace.json` 和生成的 `plugins/ios-app-release/`。这个子目录是完整且不含本机环境、密钥、App 数据的插件安装包。每次更改工具后重新打包，升级 `plugin.json` 和兼容 manifest 的版本号再分发。可加 `--zip` 生成可检查的 ZIP。

其他电脑在 Codex 中说：

> 帮我安装 https://github.com/ZpBoom/ios-app-release 中的 iOS 自动发布插件，阅读仓库的 INSTALL_FOR_CODEX.md，完成本机环境检查和配置。

Codex 可调用原生安装命令，再执行插件初始化：

```sh
codex plugin marketplace add https://github.com/ZpBoom/ios-app-release.git
codex plugin add ios-app-release@ios-release-tools --json
# 读取上一步的 installedPath，执行：
python3 <installedPath>/scripts/bootstrap.py --auto
```

此公开仓库的读取和安装不需要 GitHub 写权限或协作者邀请。其他人可以复制或 Fork 代码，但无法直接推送到原仓库。不要把 GitHub 访问令牌或 Apple 密钥写进指令或仓库。无需发布到公共插件目录。

本地安装开发版也可使用：

```sh
python3 scripts/install_plugin.py
```

它先生成干净插件包，再通过 Codex 安装，并默认执行 --auto 初始化。`--skip-setup` 可只安装插件。`scripts/install_skill.py` 是旧命令兼容入口。旧版独立安装的同名 Skill 如仍存在，需要迁移时核实并移除，以免重复。

加载安装结果以客户端提示为准，可能需要刷新或重启。插件更新可通过 `codex plugin marketplace upgrade ios-release-tools` 刷新来源，再在客户端安装/更新；不能假定刷新来源已更新所有当前聊天的运行版本。

## 每台 Mac 的一次性初始化

安装后运行插件 onboarding skill `ios-release-setup`。它检查现有环境，并按需安装依赖：

```sh
# <插件目录> 替换成实际安装路径，不是 App 目录
python3 <插件目录>/scripts/bootstrap.py --auto
```

需要完整 Xcode、Python 3.9+、现代 Ruby 3.1+ 和 Bundler。bootstrap 会复用现有环境，安装独立 Python 依赖；缺少现代 Ruby 且已有 Homebrew 时安装 Ruby 3.3，再安装 Bundler/fastlane，并把 Ruby/gem 路径保存为插件个人设置，其他开发聊天无需修改 PATH。已有完整 Xcode 但系统指向 CommandLineTools 时，可通过插件自己的 DEVELOPER_DIR 使用完整 Xcode，不改系统选择。

无 Homebrew 时由 Codex 按 https://brew.sh 官方说明协助安装；管理员密码、Xcode 许可、Apple 登录等交互由本人完成后继续。不会修改系统 Ruby，也不会运行 sudo gem install。当前这台电脑的 Ruby/fastlane 仍需按环境报告处理。

安装报告在 `~/.config/ios-app-release/setup-report.json`。退出码 2 表示依赖未全部就绪，Codex 应读取报告继续处理。依赖就绪、API 连通和真实 App 签名验证分别报告。API 配置缺失时只询问缺少的 ID 和本机路径；配置后执行 `scripts/bootstrap.py --verify-api` 做只读连通检查。

| 位置 | 内容 | 如何共享 |
|---|---|---|
| Codex 安装的插件目录 | Skill、检查器、发布工具 | Git marketplace 安装/更新 |
| `~/.config/ios-app-release/` | 本机 API 配置、独立 Python/Ruby 依赖 | 不共享 |
| 当前 App 的 `.ios-release/` | 工程绑定、版本、多语言文案、报告和构建结果 | 配置/文案进 App 仓库，报告/构建结果忽略 |

个人 `machine.json` 示例：

```json
{
  "key_id": "ABCDEFGHIJ",
  "issuer_id": "你的 Issuer ID",
  "key_path": "~/.appstoreconnect/private_keys/AuthKey_ABCDEFGHIJ.p8"
}
```

支持 Team API key 和 Individual API key，后者 `issuer_id` 留空。个人开发者会员身份与 Individual API key 是不同概念。Team key 覆盖账号内全部 App，Individual key 跟随所属用户权限。可用 `ASC_KEY_ID` / `ASC_ISSUER_ID` / `ASC_KEY_PATH` 覆盖；设置 `IOS_RELEASE_CONFIG_HOME` 可改变个人存储目录。

`.p8` 内容不进配置、Git 或聊天。每位同事使用自己的授权凭据；API 权限与本机签名权限分别配置。Xcode 证书、描述文件和账号环境仍需满足目标 App 的签名要求。

## 日常使用

在开发好了 App、已有需求和改动信息的那个 Codex 聊天中说：

> 使用 $ios-app-release，复查当前 App 的发布准备情况，判断首次上架还是更新。根据这次改动整理各语言资料，先把结果告诉我。

插件结合当前聊天上下文和可访问的项目文件使用已有资料，不会自动读取所有其他聊天历史。报告给出 App、线上状态、目标版本、阻塞项、可处理事项和未验证范围。

也可以说：

> 准备这个 App 的 1.2.0，构建号 120，检查通过后打包上传，给我看预览，先不提审。

Codex 完成多语言整理、检查和上传；你检查实际 App、文案和商店资料后：

> 我已检查 1.2.0 (120)，提交 Apple 审核，通过后等我确认上线。

审核通过后：

> 将 1.2.0 (120) 正式上线。

一条自然语言指令可以包含多个自动步骤，Apple 处理、审核和分发仍需时间。默认保留你检查、提交 Apple 审核、正式上线三个阶段。

## 检查与线上路由

| 观察到的结果 | 插件行为 |
|---|---|
| 找到唯一 iOS 主 App | 读取 Release Scheme、Bundle ID、Team ID、版本、构建号 |
| 有多个工程/Scheme/主 Target | 列候选项，依据上下文选择；仍不明确时询问 |
| 有已发布 iOS 历史 | `UPDATE`，校验目标版本和已有草稿 |
| 已有 App 记录，尚无已发布 iOS 历史 | `FIRST_RELEASE`，检查首次资料 |
| 当前凭据查不到 App | `APP_RECORD_NOT_VISIBLE`，检查权限或建立记录，不能断言不存在 |
| 缺密钥、断网或 API 失败 | `UNKNOWN`，不猜测新旧 App，不上传 |
| 目标版本已有草稿 | 复用草稿 |
| 已发布同号版本、其他版本在进行、正在审核 | 阻止直接创建/覆盖，报告状态 |
| 同号构建已上传 | 准备时自动沿用，只同步资料；处理完才能提审 |
| 远端文案与本地不一致 | 提审前停止，先核实差异 |
| 构建出口合规字段为空、文稿未关联/未获批、查询失败 | 报告列出提审阻塞；保留准备上传和同步资料能力，提审前重新检查 |
| Apple 已通过、等待开发者发布 | 在具体上线指令下发布指定版本/构建 |

新 App 记录与新版本不同。Apple 官方 API 支持创建版本，但不支持创建全新 App 记录。首次 App 记录需要通过 App Store Connect 网页建立，Codex 可在当前聊天通过可用浏览器协助填写现有资料，登录由你完成。建立后再次检查即可继续。

自动检查覆盖工程身份、版本/构建号、商店记录、版本状态、已有构建、版本级语言和文案格式，以及主 App 的出口合规配置和远端有效构建的声明状态。检查器记录 README/CHANGELOG/发布资料的位置供 Codex 复查，不会把文档存在当作内容已经验证。

### 出口合规检查

- 本地只检查选定的 Release 主 App Target 的 `INFOPLIST_FILE` 或生成 Info.plist 的 `INFOPLIST_KEY_ITSAppUsesNonExemptEncryption`；缺失或类型不是布尔值时提示，不擅自修改工程。
- 已上传且处理完成的指定构建必须有明确的 `usesNonExemptEncryption=false` 声明，或在值为 `true` 时关联 `APPROVED` 状态的出口合规文稿。字段为空、文稿缺失/未获批、查询失败均不能通过提审检查。
- `inspect` / `check` 在 JSON 和 HTML 报告中单列 `submission_blockers`，有此类待办时返回退出码 2。这类问题不阻止 `prepare` 归档或同步资料，`ready_for_prepare=true` 不表示可提审。`submit` 会重新读取远端构建及文稿状态，在关联构建和调用 fastlane 之前停止不完整的提审。
- 已上传构建缺少声明时，可在 App Store Connect 版本页的构建版本旁点击“管理”补填，无需重新上传安装包。只有核实整个 App（含第三方 SDK）无非豁免加密后，才为后续构建配置 `ITSAppUsesNonExemptEncryption=NO`；使用非豁免加密时应按真实情况配置并处理所需文稿。
- 插件验证声明完整性，不判定法律豁免，不自动填问卷、不复制上一版声明、不上传合规文稿。构建仍在处理或尚未上传时，处理完成后需再次检查。

实际功能与回归测试、签名能否成功导出、截图内容与尺寸、隐私及合规声明真实性、协议/税务/销售地区、审核账号和内购资料仍需独立验证。报告明确列出未验证范围，不保证通过 Apple 审核。

## 项目配置与命令

日常不需要手写命令。以下 `<python>` 是个人环境 `~/.config/ios-app-release/runtime/venv/bin/python`，`<插件>` 和 `<App>` 均为实际绝对路径。

```sh
<python> <插件>/scripts/project.py inspect --project <App>
<python> <插件>/scripts/project.py init --project <App> --version 1.2.0 --build 120
<python> <插件>/scripts/project.py check --project <App>
<python> <插件>/scripts/project.py prepare --project <App> --execute
<python> <插件>/scripts/project.py submit --project <App> --execute --confirm 'app@1.2.0(120)'
<python> <插件>/scripts/project.py status --project <App>
<python> <插件>/scripts/project.py release --project <App> --execute --confirm 'app@1.2.0(120)'
```

`--project` 默认当前目录，多目标工程可加 `--xcode`、`--scheme`、`--target`。`--offline` 只检查本地，不能得出线上发布类型。`inspect` / `check` 返回 2 表示待解决项，1 表示入口错误，0 表示相应操作已完成；不能把有报告当作通过。

`init` 写入 `.ios-release/apps/app/app.json` 和 `release.json`，已有文件不覆盖。默认沿用线上语言/长期文案，本次 `whatsNew` 刻意留空，由 Codex 根据实际改动起草。新增语言可用 `--locales zh-Hans,en-US,ja` 指定。

版本文案支持 `whatsNew`、`description`、`keywords`、`promotionalText`、`supportUrl`、`marketingUrl`。省略字段保留远端值，不用空字符串清空。名称、副标题属于另一个资源，本版未同步。

报告在 `.ios-release/reports/preflight.{json,html}`，预览和 IPA 在 `.ios-release/.state/`。init 为本机产物添加项目 Git 忽略规则，项目配置和多语言文案可提交。

工程应允许 `MARKETING_VERSION` / `CURRENT_PROJECT_VERSION` 覆盖主 App 的 Info.plist 版本。上传前脚本解开 IPA 再核对 Bundle ID、版本和构建号，不匹配会停止。

底层 `release.py` 仍兼容旧工作台 `apps/<别名>/` 配置。`--workspace` 可指定数据目录，插件代码和项目数据不混在一起。

## 当前边界与恢复

- 默认沿用线上截图，未实现新截图采集、设计和上传。新素材流程应依据 Apple 当前 App Asset Library 接入。
- 首次上架可能需补齐网站、截图、隐私、年龄分级、审核账号等资料，本版不猜测或自动提交声明，也未自动上传内购/订阅。
- 上传超时后检查 Apple 构建列表，`status` 只查商店版本；构建已上传可用资料同步恢复。
- 提审/发布结果未知时先查远端，不盲目重试。发布请求无法取消。
- 本机有同 App 互斥锁，尚无跨电脑分布式锁；避免同时操作同 App/版本，并分配唯一构建号。
- 公开仓库可直接读取安装。写入原仓库仍需所有者 ZpBoom 的 Git 授权，不向安装者授予写权限。

## 开发验证

```sh
.venv/bin/python -m unittest discover -s tests -v
ruby -c fastlane/Fastfile
ruby tests/test_fastfile.rb
python3 scripts/package_plugin.py
```

## 资料

- [OpenAI Docs：原生插件、Git marketplace 与本地分发](https://developers.openai.com/plugins/build/plugins)
- [Apple API 概览](https://developer.apple.com/cn/app-store-connect/api/)
- [版本级多语言资料](https://developer.apple.com/documentation/appstoreconnectapi/app-store-version-localizations)
- [Apps 资源与新 App 记录边界](https://developer.apple.com/documentation/appstoreconnectapi/apps)
- [fastlane API key 支持](https://docs.fastlane.tools/app-store-connect-api/)
