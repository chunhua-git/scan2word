# scan2word

**扫描件 PDF → 可编辑 Word（纯离线 · 免费 · 无 AGPL 依赖）**

把扫描件 PDF 拖进界面，自动识别文字并还原表格结构，输出可以直接改字的 `.docx`。
全程本地运行，材料不上传。

## 下载使用

**不需要装 Python、不需要联网、不需要独立显卡。** 下载后解压双击即可用。

| 附件 | 说明 |
|---|---|
| `scan2word.zip` | **推荐**。解压后双击里面的 `scan2word.exe`，启动快 |
| `scan2word.exe` | 单文件免解压，但每次启动会慢几秒（要先解压到临时目录） |

两个附件功能完全相同，任选其一。系统要求：Windows 10 / 11 64 位，纯 CPU，内存 4GB 以上。

下载地址：<https://github.com/chunhua-git/scan2wordv0.1/releases/latest>

## 适用范围

**核心管线不挑文档类型**：PDF 解析、表格网格重建、勾选框 / 印章 / 空白栏还原、
Word 写出——有框线的表格就是有框线的表格，跟文档属于哪个领域无关。

已实测的文档类型：

| 类型 | 结果 |
|---|---|
| 表单类文书（中文；空白模板与已填写的扫描件都测过） | 逐字比对 0.00%，表格 / 合并单元格 / 勾选框 / 空白栏全部还原 |
| 增值税电子发票 | 4×5 发票表格还原正确，买方 / 卖方信息栏对位 |
| 说明书类（含大量界面截图，24 页） | 可用，截图内文字靠 OCR，质量取决于截图清晰度 |
| 合成样本（有框线表格 / 无框线表格 / 双栏排版 / 竖排窄标签 / 8pt 密集小字） | 5/6 逐字 100%，见 `tests/test_synthetic.py` |
| 合成样本（中英数字混排） | 1.56%（`SO` 被认成 `S0`）；这类不给自动改，只报警人工核对 |

**无框线表格**（员工信息表、财务报表、简历、报价单这类一条线都不画的）也支持：
靠「行对齐 + 空白槽」推断列边界。判据里有一条关键约束——
必须「单元格文字远窄于列宽」，否则双栏排版的文章会被误切成一堆短格。

带领域色彩的部分只有两处，且**在非对应领域下不会帮倒忙**：

- `core/lexicon.py` 的形近字纠正：**只在"替换后能命中词表里的词"时才生效**。
  非对应领域下它等于不工作，但也不会把对的字改错。
  词表可扩展，见 `core/data/`（内置一份领域词表 + `自定义词表.txt` 给你加自己行业的词）。
- `core/verify.py` 的数字格式校验（证件号 / 编号 / 手机号 / 日期）：非对应文档下只是少校验几项。

**尚未实测**的类型（设计上通用，但没跑过真实样本）：竖排整篇正文、嵌套表格、
手写体、盖章遮字的表格。有样本的话欢迎提 issue。

## 解决什么问题

| 痛点 | 做法 |
|---|---|
| OCR 错字（裁判→栽判、变更→变吏、电子→匕子） | 300dpi + PP-OCRv4；带文字层的页面根本不走 OCR，直接读原文 |
| 栏目结构散架（左栏栏目名＋右栏内容） | 自研表格网格重建：横线定行、竖线定列、纵向合并自动识别 |
| 勾选框 / 印章 / 空白填写栏丢失 | Wingdings 私用区字符映射 + 光栅方框检测 + 印章裁图回填 + 填写栏下划线还原 |
| 材料敏感，不敢上传 | 全程本地运行；`tests/test_offline.py` 会把 socket 打死再跑一次转换作为证明 |
| 转完无法判断质量 | 每份输出配 `质检报告.md`，逐条列出逐字比对、数字复核、结构核对的结果 |

## 架构

引擎可替换，界面只做一次。

```
app/                    PySide6 图形界面：拖拽、批量队列、进度、忽略区域框选
  main.py               主窗口
  worker.py             后台转换线程（界面不卡）
  ignore_dialog.py      在页面图上拖框设置忽略区域

core/                   与界面完全解耦的核心
  pdfdoc.py             PDF 访问抽象层 —— 换 PDF 库只改这里
  ir.py                 中间文档模型（页 / 段落 / 表格 / 单元格 / 图片）
  lines.py              线段检测（矢量线 & 光栅线）与几何工具
  grid.py               表格网格重建：线段 → 单元格（支持 rowspan / colspan）
  textlayer.py          矢量路：有文字层的页面零误差还原
  ocr.py                OCR 路：渲图 → 纠偏 → 识别 → 线检测 → 勾选框 / 印章 / 空白栏
  engines/              OCR 引擎接口 + 注册表（RapidOCR 默认，PP-StructureV3 可插）
  number_audit.py       数字字段换分辨率二次识别
  verify.py             逐字比对、数字格式校验（身份证校验位 / Luhn / 编号）
  docx_writer.py        IR → .docx（合并单元格、勾选框、印章、字体）
  searchable_pdf.py     双层可搜索 PDF（reportlab 隐形文字层）
  pipeline.py           编排：分流 → 转换 → 报告

tests/
  acceptance.py         验收套件：对真实样本逐条打分，产出验收报告
  test_synthetic.py     合成扫描件测试：自己造样本，因此有标准答案，能算真实错字率
  test_offline.py       离线证明：把 socket 打死跑转换
  test_lexicon.py       词表纠错回归（这条路径曾经带着潜伏的 NameError 上线）
  smoke_gui.py          界面冒烟测试（离屏跑一次真实转换）
  run_vector.py         矢量路结构自检

packaging/
  scan2word.spec        PyInstaller 配置（目录版）
  scan2word_onefile.spec PyInstaller 配置（单文件版）
  build.ps1             一键打包
  post_build.py         附带说明 / 许可证、统计产物、校验模型是否进包
  verify_exe.py         用真实样本自检打包产物
```

三个引擎接缝：换 OCR 引擎改 `core/engines/`，换 PDF 库改 `core/pdfdoc.py`，
换版面算法改 `core/grid.py`，其余代码不用动。

## 双路分流

准确率的关键在于「能不 OCR 就不 OCR」：

```
一页 PDF
  ├─ 有文字层？ → 矢量路：直接读文字 + 读矢量线条还原表格
  │                 与原文逐字比对，错字率 0.00%，0.2~0.4s/页
  └─ 图片型？   → OCR 路：300dpi 渲图 → 纠偏 → RapidOCR → 光栅线检测
                    → 网格重建 → 勾选框 / 印章 / 空白栏 → 段落聚合
                    → 数字字段换分辨率复核，12~16s/页
```

同一份 PDF 里两种页面混排也能处理，逐页自动选路线。

## 使用

### 直接运行打包版

从 [Releases](https://github.com/chunhua-git/scan2wordv0.1/releases/latest) 下载 zip，
解压后双击 `scan2word.exe`。也可以把整个文件夹拷给别人，对方不需要装任何环境。

### 从源码运行

依赖装在项目根的 `vendor/` 目录（不改动系统环境）：

```powershell
python tools\cli.py "你的文件.pdf" --outdir out --searchable-pdf
```

### 运行测试

测试需要自备样本 PDF，放到项目根的 `samples/` 目录下（仓库里不含样本，
因为都是真实当事人材料）。没有样本时脚本会提示并跳过。

```powershell
python tests\acceptance.py      # 验收：对 samples/ 下每个样本逐条打分
python tests\test_synthetic.py  # 合成样本测试（不需要真实样本，自带标准答案）
python tests\test_offline.py    # 离线证明
python tests\test_lexicon.py    # 词表纠错回归（不需要样本）
python tests\smoke_gui.py       # 界面冒烟
python tools\review.py          # 交付前审查：语法 / 未用导入 / 过期引用 / 功能对账
```

### 打包

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

流程：PyInstaller 打包 → 附带说明与许可证 → 用真实样本自检产物 exe。
产物在 `dist/`：目录版、单文件版、绿色版 zip。

## 许可证

本项目代码 MIT，见 `LICENSE`。第三方组件的许可证与注意事项见
`THIRD-PARTY-NOTICES.txt`，全部为宽松许可，无 AGPL：

| 组件 | 许可证 |
|---|---|
| RapidOCR / PP-OCRv4 模型 | Apache-2.0 |
| onnxruntime | MIT |
| pypdfium2（PDF 渲染） | Apache-2.0 / BSD-3-Clause |
| pdfplumber / pdfminer.six | MIT |
| reportlab（双层 PDF） | BSD-3-Clause |
| python-docx | MIT |
| PySide6（界面，动态链接） | LGPL-3.0 |
| OpenCV | Apache-2.0 |
| NumPy | BSD-3-Clause |

> 早期版本用 PyMuPDF 做 PDF 访问。它是 **AGPL-3.0**：把打包产物发给别人等于分发副本，
> 必须连带以 AGPL 提供全部源码，与「可自由改、可闭源分发」冲突。
> 现已整体替换为上表中的组件，并实测替换前后中文文字层逐字一致、
> 矢量表格网格完全相同（对比脚本见 `tools/compare_pdf_backends.py`）。

## 实现注意事项

这些都是实际调过的，改代码时容易再踩：

1. **`insert_text` 按空格折行**：中文整行没有空格，会被当成「一个超长单词」静默截断，
   双层 PDF 的文字层因此丢掉半句。改用 `TextWriter` / `textOut` 整行写入。
2. **折行合并后的 bbox 是多行并集**：用 `bbox.height` 反推字号会算出 28pt，
   长行冲出页面被裁掉。字号必须取 run 上记录的真实字号。
3. **pdfplumber 的 `extract_text_lines()` 只按 y 带分组**：会把左栏竖排栏目名和
   右栏字段名并成一条，落到表格里就串格。需要在行内再按**字距**切一刀。
4. **python-docx 的 `table._cells` 会重复列出被合并覆盖的格子**（11×5 的表能报出 55 个），
   统计结构时要按 `_tc` 去重，否则会把正确的合并误判成结构不一致。
5. **勾选框检测必须要求「有紧邻文字宿主」**：身份证底纹会让它误报 96 个假框，
   足以毁掉整篇文本。
6. **`det_limit_side_len` 不是越大越准**：实测 1600 比 1280 又慢又多认一个错字。
7. **Windows PowerShell 5.1 按 ANSI 读无 BOM 的 .ps1**：脚本里写中文会变乱码并报
   语法错。构建脚本保持纯 ASCII，中文路径交给 Python 处理。
