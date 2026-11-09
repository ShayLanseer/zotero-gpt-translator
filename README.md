# Zotero GPT 全文翻译

面向本地桌面 Codex 的论文全文翻译插件。由当前聊天模型逐段翻译，Python 脚本负责读取 PDF、校验翻译计划、排版和将译文添加到 Zotero 原条目。

当前插件版本：**0.1.11**。这是 Codex 插件，不是安装进 Zotero 的 `.xpi` 扩展。更新内容和性能边界见 [CHANGELOG.md](CHANGELOG.md)。

## 能做什么

- 生成纯中文正文 PDF，尽量保持原页数、页面尺寸、栏位和段落位置。
- 保留原图、表格内部内容和独立公式；行内公式及数学符号可从原页裁取保留。
- 迁移文献引用、交叉引用和外部链接的目标，并按译文位置重建点击区域。
- 检查文字遗漏、重复、越界、字号缩小和区域覆盖；排版失败时停止并保留工作文件。
- 在完成语义及视觉复核后，将译文作为新附件添加到 Zotero 原条目，保留原 PDF。
- 支持分段译稿、断点继续，以及经过散列校验的部分缓存复用。

脚本不调用翻译模型 API，无需另外配置翻译 API Key；翻译仍使用当前聊天模型的服务与额度。它需要模型判断段落和公式区域、完成译文及复核，不是独立运行的一键自动翻译程序。

## 环境要求

- 有本机文件读写和命令执行能力的桌面 Codex，以及可用的 `codex` 命令。
- Windows：默认排版需要本机宋体（SimSun）与 Times New Roman。仓库不分发系统字体；其他系统尚未在本发布副本中验证。
- Python：本发布副本使用 Python 3.12 检查；依赖见 [requirements.txt](plugin/skills/zotero-gpt-translate/scripts/requirements.txt)。
- 使用 Zotero 文库功能时，保持 Zotero Desktop 运行，并在其设置中启用“允许此计算机上的其他应用程序与 Zotero 通信”（名称可能因语言或版本略有不同）。脚本仅接受 `http://127.0.0.1:23119/api`，当前仅支持本机个人文库 `/users/0`。
- 也支持直接处理本地 PDF；这种方式没有 Zotero 父条目绑定，不能直接自动附回文库。

## 安装

以下为 Windows PowerShell 示例。在下载或克隆后的仓库根目录运行。若下载 ZIP，先完整解压，并保留 `.agents`、`.codex-plugin` 等以点开头的目录。

### 1. 安装本地 Python 依赖

```powershell
py -3.12 -m pip install --target .runtime -r .\plugin\skills\zotero-gpt-translate\scripts\requirements.txt
```

仓库不附带 Python 或第三方依赖二进制文件；上述步骤需要联网下载依赖。

### 2. 生成本机依赖配置并检查

```powershell
$depsPath = (Resolve-Path .\.runtime).Path
@{ deps = $depsPath } | ConvertTo-Json | Set-Content -Encoding utf8 .\plugin\skills\zotero-gpt-translate\scripts\runtime.local.json
py -3.12 .\plugin\skills\zotero-gpt-translate\scripts\translate.py doctor
```

确认输出包含 `"ready": true`。这只说明 Python 依赖可用，不代表 Zotero 连接或全文翻译已验证。`runtime.local.json` 记录当前电脑的绝对路径，已在 `.gitignore` 中排除；不要上传或直接复制到另一台电脑。请保留 `.runtime`。若移动仓库或更换电脑，重新生成配置并刷新插件安装。

### 3. 添加本地插件目录

```powershell
codex plugin marketplace add .
```

然后重启桌面应用，在插件目录中选择 **Zotero GPT Translator** 来源，安装“Zotero GPT 全文翻译”，再新建聊天。请先完成依赖配置，再安装，让安装副本包含当前机器的配置。

安装目录配置见 [.agents/plugins/marketplace.json](.agents/plugins/marketplace.json)。本地目录安装方式依据 [OpenAI 官方插件文档](https://developers.openai.com/plugins/build/plugins)。若你的客户端没有相应入口，请核对客户端支持情况，不能将“代码下载成功”视为“插件安装成功”。

## 开始使用

在新聊天中选择插件，并给出明确论文标题：

> 使用 $zotero-gpt-translate，把 Zotero 中《论文标题》的英文原论文全文翻译成中文 PDF，尽量保持原页排版、公式图表和链接，并添加到原条目。请先核对原附件，完成后检查全部页面。

首次使用新工作区时，模型需先建立只读连接。也可手动在选定的论文工作区运行：

```powershell
py -3.12 PATH_TO_SCRIPT\translate.py connect --workspace .
```

这里 `PATH_TO_SCRIPT` 是本仓库 `plugin/skills/zotero-gpt-translate/scripts` 的实际绝对路径，需自行替换。`connect` 创建该工作区的 `.zotero/connection.json`，不申请文库写入权限。首次实际添加附件可能触发 Zotero 授权对话框，由使用者确认。不要在仓库中提交连接配置、文库数据或授权信息。

## 字体准备与更换

### 字体由使用者自行提供

仓库不包含 `.ttf`、`.ttc` 或 `.otf` 字体文件。请使用自己有权使用并嵌入 PDF 的字体；将字体随软件公开分发与将字体嵌入 PDF 的授权条件不同。Windows 字体的相关说明见 [微软字体分发 FAQ](https://learn.microsoft.com/en-us/typography/fonts/font-faq)。不要把本机系统字体提交到本仓库。

当前默认正文样式使用以下字体：

| 用途 | Windows 默认来源 | 每篇论文的工作目录文件 |
| --- | --- | --- |
| 中文正文 | `%WINDIR%\Fonts\simsun.ttc` 中的 SimSun | `math-assets/font-simsun.ttf` |
| 普通英文文字 | `%WINDIR%\Fonts\times.ttf` 中的 Times New Roman | `math-assets/font-times.ttf` |

公式、数学变量、图表等按原论文内容保留，不跟随正文更换字体。

### 使用本机默认字体

完成该篇论文的 `prepare` 后，在仓库根目录执行以下示例。将 `WORK` 替换为这篇论文的实际工作目录：

```powershell
py -3.12 .\plugin\skills\zotero-gpt-translate\scripts\translate.py fonts --work "WORK"
```

命令从当前 Windows 的字体目录准备上述两个文件。宋体来源是 TTC 字体集合，脚本会提取其中的单个字体，不能只把 `.ttc` 后缀改成 `.ttf`。若文件已存在，命令会核对字体名称并保留，不自动覆盖。

### 自行更换字体文件

字体文件按每篇论文分别存放。需要更换时，先备份该论文工作目录中的两个字体文件，再将自行准备且获授权的字体放入对应位置。使用同一字体家族的不同合法版本时，也需核对内部字体名称和字形是否满足要求。

**0.1.9 的默认流程校验 SimSun 和 Times，尚无任意字体路径或字体名称的配置选项。** 将其他字体重命名为 `font-simsun.ttf` 或 `font-times.ttf`，不能使它成为受支持的替代字体：`fonts` 会检查内部名称，默认宋体正文的 `build` 还会核对生成 PDF 中是否实际使用 SimSun。`runtime.local.json` 的 `deps` 字段仅配置 Python 依赖目录，不配置字体。

若要改成其他中文或英文字体，需要自行适配 `scripts/translate.py` 中的 `prepare_fonts`、字体加载及生成后校验逻辑，并同步调整技能中的默认字体要求。使用非默认字体的方案尚未验证；不要通过伪造字体名称或直接移除校验来绕过检查。

更换字体后，对受影响的论文重新执行 `build`、`render` 和完整复核，确认字形、缺字、行距、分页及公式邻近内容，再执行 `review`。旧的复核记录不能用于更换字体后的 PDF。缺少可用字体时，先完成字体准备，不静默替换为其他字体或降低校验要求。

## 工作流程与命令

1. `doctor` 检查依赖；`connect` 绑定当前电脑的 Zotero 实例。
2. `search` / `inspect` 核实条目、原文附件和父条目。
3. `prepare` 提取原页、文字、链接、预览与分段稿。
4. 由聊天模型阅读原文并完成译文、保留区域及公式和引用标记；`compile` 将分段稿转为逐页计划。
5. `fonts` 从本机准备字体；`build` 自动执行完整验证与批量排版预检，通过后生成 PDF，再用 `render` 输出预览。单独排查问题可运行 `preflight --work WORK`，并集中处理 `preflight.json` 中的错误。0.1.11 起预检会生成并验证实际公式裁图，按验证后的尺寸排版；未经验证的旧裁图不能直接复用。
6. 核对全部页的语义与视觉效果，完成 `review` 后，在用户授权下执行 `attach`。

`draft` 可重新生成分段草稿，继续任务前先核对已有译稿，避免覆盖。更详细的数据格式和操作要求见 [SKILL.md](plugin/skills/zotero-gpt-translate/SKILL.md) 与 [workflow.md](plugin/skills/zotero-gpt-translate/references/workflow.md)。

## 限制

- 英文和中文的长度及词序不同，不能保证逐字同位或任何论文都完全等版式。
- 扫描件、损坏文字层、特殊字体和密集混排可能需要 OCR、人工区域判断或额外处理。
- 自动检查不能证明翻译语义正确；保留公式裁图也需要核对字形、上下标和基线。
- 不保证处理 PDF 的所有特殊互动行为。过密内容无法放入原位置时需要调整，而非静默加页或删减信息。
- “无需翻译 API Key”不表示模型服务免费，也不表示论文内容不进入当前聊天模型。Python 脚本负责本机处理，模型阅读的内容由所用聊天服务处理。

## 开发与测试

仓库保留原有离线行为测试，使用合成 PDF 和模拟 Zotero API，不应连接真实文库。

```powershell
$env:ZGT_DEPS = (Resolve-Path .\.runtime).Path
py -3.12 -X utf8 .\tests\test_translation.py
```

测试产物位于 `test-output/`，不提交到仓库。当前版本通过 23 项离线行为测试，覆盖几何约束、链接、公式裁图像素与边界检查、邻近原生公式保留、字体、缓存、复核失效、模拟附件重试、批量排版失败报告和预检实际公式隔离等行为；通过测试不等于任意真实论文的端到端翻译已通过。

## 仓库内容

```text
.agents/plugins/marketplace.json  本地插件安装目录
plugin/.codex-plugin/plugin.json 插件清单
plugin/skills/                   工作流程、实现和格式说明
tests/test_translation.py        原有离线行为测试
THIRD_PARTY.md                   第三方依赖与字体说明
```

本仓库不包含论文、译文、个人 Zotero 连接配置、依赖包或系统字体。项目代码的分发许可证待作者确定；第三方组件的授权独立适用，见 [THIRD_PARTY.md](THIRD_PARTY.md)。
