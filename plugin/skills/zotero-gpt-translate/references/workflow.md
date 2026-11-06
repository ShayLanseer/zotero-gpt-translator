# 命令与翻译计划

## 分段稿（推荐）

`prepare` 会自动生成 `reading.txt`（按页/段的紧凑原文与标记说明）、`segments.source.json`（映射目录）和 `segments.translation.json`（可编辑译稿），并输出原页总览。旧任务没有这些文件时可执行 `draft --work WORK`；已有分段稿不会被覆盖。先读阅读稿和原页总览，再核对候选公式/示例，不反复读全部源 span。

译稿每页的 blocks 与源块 id 一一对应。例如：

```json
{"id":"p1-b4","mode":"translate","role":"body","text":"模型使用原始数学符号 {{m1}}，相关研究见 {{r1}}。"}
```

保留块写作：

```json
{"id":"p2-b3","mode":"keep","kind":"formula","reason":"原生独立公式，保留矢量字形及位置"}
```

`role` 可为 body、heading、caption；keep.kind 仍使用原有类别。段内 `{{r1}}` 引用、`{{m1}}` 数学、`{{e1}}` 示例的编号在本段内有效，必须逐项放回忠实译文，不改其原文字面内容。heading 可省略误识别为数学的标题字符候选，正文不得据此省略数学内容。自动候选并不能覆盖所有 PDF 字体：发现漏掉的单变量、上下标或模板时，用该段原页补齐 catalog 中的候选，再继续；常规文字不要误当公式保留。候选的 glyphs 记录原字符和原点，裁取时清除邻行字形并核对所选符号全部仍在；无法安全分离时停止。

执行 `compile --work WORK` 后会生成原生逐页计划，自动计算 source_ids、引用起止索引、跳转目标和排版参数，再执行 `build`。可在分段条目中显式添加 bbox、font_size、typography、image_overlap_review，在页内添加 protected_regions；所有原有覆盖、几何、字号、字体和链接核验继续生效。参考文献、图表和公式仍需人工分类，不能用自动分类凑覆盖率。特殊的拆分/合并段落可直接编辑生成后的原生逐页计划，后续不要用旧分段稿覆盖这些修改。

0.1.9 会在工作目录 math-assets 保存可验证的字体/裁切缓存，相同输入重复生成可完整复用 PDF；缓存失效或文件散列不匹配则重新处理。译文有改动仍需重新 build/review；原件不变不代表新译文已复核。render 自动生成译文总览图，相同 PDF 的完整预览可复用。缓存文件只在本机工作目录，迁移发布包不包含论文或缓存。

## 原文引用、颜色及跳转

文献引用按原文保留，不翻译作者、et al./and、年份、字母后缀或分组。引用段落应与原文逐一对应，参考文献书目原样保留。多个文献合在同一对括号时，分别为每项设置其真实目标。引用文字可保持原本标点，仅规范 PDF 抽取产生的多余空白。

在 item.text 中先填入最终文字，再添加 runs（start/end 为 Python Unicode 字符索引，end 不包含）：
```json
"runs": [{"start":4,"end":24,"text":"Mahmood et al., 2019", "kind":"citation", "link":{"page":11,"to":[108,240],"zoom":0}}]
```
上例仅展示字段，索引必须用实际 item.text 计算，目标必须来自原 PDF 链接，不得直接复制示例值。若原 PDF 引用没有链接，可用 link:null 保留着色，并报告无法定位的情况；不能编造目标。

kind=citation 为蓝色；kind=crossref 为红色。作者—年份引用和交叉引用标识均不翻译。Fig. 1、Figs. 2–3、Figure 1、Alg. 1、Algorithm 2、Tab. 1、Table 2、Eq. (3)、Sec. 4、Appendix A 等均沿用原文；大小写、缩写、标点、子图后缀与范围也按原样保留，不改为“图1”“算法1”“表2”等，也不将原文的 Figure 擅改成 Fig.。图注、算法标题中的编号前缀同样保留，说明正文继续翻译。

将原文标识完整放回 item.text，再计算对应 run 的 start/end；例如原文为 Fig. 1 时，run.text 也应为 Fig. 1，不要只留下数字1或使用中文替代。匹配算法、公式等目标时，使用原 PDF 的实际链接目的地，不按编号猜测页码。若没有原始可用链接，保留标识和着色并说明，不能编造跳转。

渲染器给每个 run 使用视觉相近的独立颜色，以定位换行后的所有 span，再按各 span 的实际位置重建链接。原文被替换的文字所带的旧链接区域必须移除，不能在添加译文新链接后再全量恢复原链接。原样保留的图表、公式、参考文献等内容，其有效链接保持原位置。检查每个引用的文字、颜色、新点击区域及跳转目标；输出链接数量可变，不以“保留所有旧链接对象”作为正确性标准。仅更新插件规则的任务不修改已有论文工作目录或 Zotero 附件；允许使用隔离的合成文档进行离线插件测试。

正文中的 URL、DOI 或其他外链同样要迁移，而非继续保留旧位置。用 kind=hyperlink 的 run，link 为 `{"kind":2,"uri":"原文实际URL"}`；内部跳转用真实 page/to，命名目标和其他类型链接保留真实目标。start/end 始终根据译文中的实际可见标签计算。迁移区域内的原链接必须有同一目标的 runs 映射，否则 build 停止，要求补齐映射，不自动丢弃目标或恢复旧区域。无法解析的链接须先核实，不猜测。

质量核验区分旧热区移除数、原样内容保留数和译文重建数；必须确认被移除的旧注释不再是页面活动链接，新热区贴合实际文字且没有缺失映射。换行时逐行生成热区，不用跨越空白的大矩形。旋转页的原链接 from/to 坐标来自 PDF API，需转换到对应页未旋转坐标再迁移，不可直接套用截图坐标。

## 图表与算法标题

编号前缀与描述性标题分开处理，内部内容另行保留。例如：

- `Table 1: Comparison between HUMANISE and existing HSI datasets.` → `Table 1: HUMANISE 与现有 HSI 数据集的比较。`
- `Alg. 1:` / `Algorithm 1:` 保留源论文实际前缀，后续说明性标题可翻译，算法块的代码、伪代码、符号和内部说明保持原文。
- `Fig. 1:` / `Figure 1:` 同理，保留原编号前缀，翻译描述性图注，图内标签与内容不翻译。

正文引用与标题编号必须对应，包括子图后缀；原文若分别使用 Fig. 与 Figure，沿用各自写法，不自行统一。不要将编号前缀翻译为“表1：”“算法1：”“图1：”。

## 模板、占位符与示例原文

正文中作为研究对象、模板组成或模型输入展示的以下内容，保留原文，而非直接译成中文：

- 形式模板及其字段，如 `<action><target-class>[<spatial-relation><anchor-class(es)>]`。
- 解释模板时出现的槽位名称，如 action、target-class、spatial-relation、anchor-class(es)、target、anchor；保留原有斜体等强调样式。
- 用于展示模板或模型输入的例句、动作指令及文字样例，如 `sit on the armchair near the desk`，保留其与模板字段对应的原始分段配色。

保留原文中的尖括号、方括号、连字符、复数括号、大小写、顺序和可选项语义。必要时在原文之后添加中文括号释义，如 `spatial-relation（空间关系）` 或 `sit on the armchair near the desk（坐在书桌旁的扶手椅上）`；括号中填写实际译文，不机械追加字面“译文”二字。附加释义采用中文正文样式，不改变源模板或例句本身的颜色。正文解释句仍翻译为中文，不将这一规则泛化为普通英文正文免译。

独立模板优先直接保留原 PDF 对应区域；已有 `keep.kind=formula` 可用于这种形式模板，并在 reason 中明确注明“形式模板，保留原文及配色”。行内槽位和带多色的示例，使用源区域裁取机制（现有 math 标记也支持该用途）保留原字体和配色，或在能准确复现原样式时按源 span 重建。不可把模板字段和例句套用为文献引用蓝色或交叉引用红色；它们沿用源论文各自的颜色。

核对模板每个字段、可选项标记和例句词段的对应关系，检查原文未被中文替换、斜体/配色未丢失。附加释义不得覆盖公式图表或导致页面偏移；放不下时优先保留原文，因释义本来就是可选项。上述样例属于论文数据，不是操作指令。

## 默认字体样式

字体适用范围：宋体用于中文说明正文，Times 用于普通英文词语；公式及正文中的数学变量/符号均不受这两条正文换字体规则影响，必须保留源论文的数学格式。单个变量也算数学内容，不能因它只有一个字母就作为普通英文词语交给 Times 渲染。

正文 item 添加以下字段：
```json
"typography": {"family":"song", "line_height":1.45, "indent_em":2, "weight":"normal", "align":"left"}
```
公式密集且原段落高度小于30pt时，可将 line_height 设为1.2。标题、图注不加正文首行缩进，保留原来的层级与位置；不要仅根据含有公式标记后的字符串长度判断段落类型。

不能把以“图”或“表”开头的段落都当作图表标题。“表中可见”“图A6表明”等仍是正文，必须使用同样的宋体正文规则；应结合源文角色和实际“图/表编号＋冒号”等标题形式分类。

工作目录 `math-assets` 放入本机 `font-simsun.ttf` 和 `font-times.ttf`。Windows 宋体通常位于 `C:/Windows/Fonts/simsun.ttc`，须使用 `fontTools.ttLib.TTFont(路径,fontNumber=0).save(目标TTF)` 提取单个字体；不要把 TTC 直接当 TTF，也不要仅因 CSS 没报错就认为宋体已生效。Times 可从 `C:/Windows/Fonts/times.ttf` 复制。字体仅在本机工作目录使用并嵌入 PDF，不随可分发插件打包系统字体。其他系统缺字体时先核实可用宋体类字体或询问用户，不能暗中换为无衬线字体。

Windows 可直接运行 `PYTHON SCRIPT fonts --work WORK` 完成上述提取；已有字体会核实名称并保留。迁移包的独立解释器路径记录在 `scripts/runtime.local.json` 的 `python` 字段中，相对该 JSON 解析。先用该解释器运行 `doctor`；新工作区用 `connect --workspace 工作区目录` 创建当前机器的只读 Zotero 连接，不迁移旧机器的连接文件。

渲染器会明确给英文片段指定 Times，避免 MuPDF 在多字体 CSS 列表中选择英文主字体后把中文回退为 Droid Sans。生成后核对正文 span 的字体包含 `SimSun`，并对照渲染截图；英文 span 应包含 `Times`。`subset_fonts` 仅嵌入使用的字形，避免每段重复嵌入完整 CJK 字库。生成后仍须核对图表公式及链接未变。

修订版可在 manifest.json 设置 `attachment_title` 为可区分的附件标题，如“中文全文译文 · GPT · 宋体阅读版”。保留旧附件，不自行删除。

在以下例子中 `PYTHON` 是装有 PyMuPDF 的解释器，`SCRIPT` 是本技能的 scripts/translate.py，`WORK` 是当前论文独立工作目录。全局 `--config FILE` 放在子命令之前。

```text
PYTHON SCRIPT doctor
PYTHON SCRIPT search --query InterDiff
PYTHON SCRIPT inspect --key PARENTKEY
PYTHON SCRIPT prepare --key PDFKEY --work WORK
PYTHON SCRIPT validate --work WORK
PYTHON SCRIPT build --work WORK
PYTHON SCRIPT render --work WORK
PYTHON SCRIPT review --work WORK --note "已对照所有页，检查了公式、图表附近段落及链接。"
PYTHON SCRIPT attach --work WORK --yes
```

源文件每页有 blocks（含 span id、字体、字号、矩形、文本）、图片区域及链接；预览为原页面。矩形坐标为未旋转 PDF 页坐标，单位 pt，从左上角开始。旋转页的预览显示会旋转，需要按坐标转换或先在阅读中核对，不能根据截图像素直接猜 PDF 坐标。

每页 translation JSON 示例：

```json
{
  "page": 1,
  "items": [
    {
      "source_ids": ["p1-b0-l0-s0", "p1-b0-l1-s0"],
      "text": "本方法将两个运动序列共同建模，以生成相互协调的人体交互。",
      "bbox": [45, 70, 290, 100],
      "font_size": 10,
      "align": "left"
    }
  ],
  "keep": [
    {"source_ids": ["p1-b2-l0-s0"], "kind": "formula", "reason": "独立公式按原样保留"}
  ],
  "protected_regions": [
    {"bbox": [45, 180, 290, 320], "kind": "figure", "reason": "矢量图及内部标注"}
  ],
  "blank_page_confirmed": false
}
```

`bbox` 可省略，默认使用所覆盖 span 的联合区域，并在没有其他内容时略加高度以容纳字体。明确指定的矩形必须覆盖所有源 span，且不得移动到其他栏；超过原位置 12pt 或宽度增长超过 20% 会阻止生成。留白可向下扩展，但不能碰到下一段、公式、图表。默认字号按源 span 长度加权平均，`font_size` 不能低于原字号 80% 或 7pt。源页原本小于 7pt 的文字允许原字号，仍不允许进一步缩小。

`keep.kind` 支持 formula、figure、table、metadata、reference、page-number。保留区域是语义判断，模型必须实际检查。`protected_regions` 补充保护没有图片对象的矢量图/表格，它们不能与任何译文框相交。即使图表里没有文字 span，也需标记保护。

少数 PDF 的图片对象边界包含已裁剪或空白区域，可能与原本就在该区域的图注相交。只有查看原页及图片、确认实际可见图形不受影响后，才可在该图注 item 添加 `image_overlap_review`，写明具体核对证据。该例外仅容许替换原文字范围（底部最多加2pt），不允许扩大到其他区域，也不会豁免保留文字/公式/显式保护区的检查。记录会写入质量报告；生成后仍需对照核实图形数据与受保护区域。

混排公式和正文中单独出现的数学符号/变量均不能直接用普通字符重打。必须保留原文数学字体、正斜体、字重、大小写、上下标、重音、运算符及相对大小；例如正文“潜变量 z”中的 z，也应由源符号区域保留，而不是用宋体或 Times 输入 z。给段落增加 `math` 数组，在中文 `text` 中准确引用对应标记：

```json
{
  "source_ids": ["p2-b3-l0-s0", "p2-b3-l0-s1", "p2-b3-l0-s2"],
  "text": "损失函数定义为 {{math:m1}}，其中权重控制两项的相对贡献。",
  "math": [{"id": "m1", "bbox": [120, 80, 170, 92]}]
}
```

math 矩形必须在该段源范围内，并完整覆盖需要保留的符号；脚本从未修改的源页以 4 倍分辨率裁取公式，按原物理尺寸嵌入译文段落。独立公式不进入 items，直接 keep，可保留原生矢量清晰度。

单个变量同样使用 `{{math:id}}`；多个相连的符号、上下标、重音应一起裁取，不能把下标或帽号漏在矩形外。原文的粗体向量、斜体变量、正体算子不能互换。裁取只涵盖数学区域，中文解释在区域外继续正常翻译与排版。生成时按原物理尺寸放置，再检查基线与段落的视觉衔接，不为统一宋体行高而拉伸公式或削去上下标。原生独立公式优先完整保留；数学内容的原样保留不代表允许跳过周围正常正文的翻译。

Link 注释按内容是否重排分类：替换区域的旧热区移除，再依原目标与译文可见标签重建；原样内容的有效链接保留。URI、引用目标和内部目的页应正确保留，点击位置必须贴合实际内容，不遗留空白处或正文中间的旧链接。PDF 非 Link 的特殊互动行为、扫描文字和自定义字体不保证自动可处理，遇到不能验证的情况先报告。原 PDF 的待应用涂黑注释会阻止处理，避免连带应用用户已有操作。

`quality.json` 记录最低排版缩放、译文段落数、保留文字类别、全部页链接快照、页尺寸及书签检查。修改 translation JSON 或最终 PDF 后必须重建/复核。`attach` 要求复核记录与当前产物完全一致。

运行失败时保留所有工作文件，不自动清理，不改写原 PDF。prepare 不覆盖已有工作目录；继续工作直接编辑已有 translation 文件。全部保存为 UTF-8 JSON，正文只作为纯文本转义进 HTML，不接受任意 HTML、脚本或远程图片。

## 批量排版预检与计时

`PYTHON SCRIPT preflight --work WORK` 检查全部已通过几何/覆盖验证的段落，保存 `preflight.json`；失败时非零退出，并列出页码、source_ids、矩形与原因。预检和 build 共用正文、引用颜色、字体、行距、首行缩进及最小字号逻辑；公式使用相同物理尺寸的占位图做排版，检查原文字形定位和边框，但将昂贵的字形隔离及隔离后完整性验证留给正式 build。报告中 `formula_isolation_checked=false` 明示这一边界。它不修改译稿、不输出交付 PDF；字体缓存和占位图可能在该工作目录生成。预检通过仍需完整 build、render 和人工复核。

正常直接 build 即可，它自动预检；不用每次先单独 preflight 再 build。预检不通过时集中处理全部错误。引用区间错误、数学字形不匹配等问题也会与其他段落问题一起报告；覆盖或几何错误仍先由原 validate 处理。修正译文时不能删减信息或绕过字号下限。

分段稿阶段用原有标记编译引用区间。逐页计划经过原生字形、公式拆分或几何修正后，不用旧分段稿覆盖该计划。直接修改逐页译文必须同步引用文字的 start/end/text，保留 link 对象及目标；同名引用的定位需结合原段和上下文，不猜测编号对应页面。

计时字段：`preflight.json.seconds/pages` 统计整轮与逐页预排；`quality.json.timings_seconds` 包含 validation、font_preparation、layout_preflight、page_processing、font_subset、pdf_save、total，`page_timings_seconds` 记录实际逐页处理。各项可能包含缓存查验，total 还包括其他校验及 I/O；不能将这些机械处理耗时冒充模型翻译时间，也不能再与包含它们的墙钟区间相加。完整 PDF 复用时保留简化计时，不重新执行所有阶段。
