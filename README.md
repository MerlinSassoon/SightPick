# SightPick（视取）· 视觉 RPA 工程样品

> 把 GUI 当成 API：**截图 → OCR → 视觉大模型 → 结构化数据**。
> 当前案例：BOSS直聘岗位采集；核心抽象可迁移到其他没有 API/DOM 的桌面或网页系统。

**English summary**
SightPick is a visual RPA engineering sample. It captures regions of a GUI, recognizes text with PaddleOCR, uses a vision LLM for semantic checks and denoising, and writes structured records. BOSS直聘 is the first case study. The two modes — **list navigation** (historically called “SMS mode”) and **region transcription** — are domain-agnostic primitives, not a job-board-only design.

---

## 这是什么 / 不是什么

**是：**

- 一个可运行的视觉 RPA 参考实现：区域配置、截图/点击/滚动、OCR、视觉大模型、异步 worker、JSONL 增量落盘；
- 一个“没有 API/DOM 时，如何把屏幕变成数据”的工程样品；
- 一个以 BOSS直聘为案例、但把领域差异收敛到配置层的架构演示；
- 一套带真实运行证据、也带已知限制的诚实的原型。

**不是：**

- 不是开箱即用的招聘爬虫，也不是商业数据产品；
- 不是绕过登录、验证码或平台访问控制的手段；
- 不是“完整可视化平台”；当前只有只读结果预览；
- 不与 BOSS直聘存在任何隶属或合作关系。

---

## 状态一览

| 项目 | 状态 |
| --- | --- |
| 真实链路（pyautogui + PaddleOCR + DeepSeek） | ✅ 已跑通；7 次 real + DeepSeek 成功批次 |
| 稳定批次 | ✅ 最近连续 5 次：19 条记录，约 367 秒，0 warning |
| 单次最大采集量 | 5 条（**20 条验收尚未完成**） |
| 单元/契约/端到端测试 | ✅ 213 passed, 1 skipped（本地复跑） |
| 描述重复行 | ✅ 35 条真实记录 0 重复 |
| 标题/描述质量 | ⚠️ 仍需治理；标题缺陷、描述噪声/PII 已量化 |
| 20 条 / ground truth / 成本 | ⏳ 尚未完成 |
| 隐私与合规 | ⚠️ `runtime/` 含真实截图与招聘者信息，**不可上传** |
| License | ⚠️ 尚未选择；分发前需补 |

> 真实运行证据、口径和限制见 [docs/EVIDENCE.md](docs/EVIDENCE.md)。
> 当前数据质量审计与目标见 [docs/EVIDENCE.md](docs/EVIDENCE.md) 和 [docs/CASE_BOSS.md](docs/CASE_BOSS.md)。

---

## 为什么是视觉 RPA

视觉 RPA 不是“更高级的爬虫”，而是一种**在接口不存在时的最后手段**。它的价值与代价都很明确：

| 场景 | 适配度 | 原因 |
| --- | --- | --- |
| 老旧 Windows 客户端 / 企业内部系统 | **强** | 没有 DOM/API，屏幕是唯一接口 |
| 需要跨多个视觉步骤的流程 | **强** | 点击、滚动、确认、抄录可以组合 |
| 页面结构频繁变化、但视觉布局稳定 | **中** | 重新框选即可，不必改选择器 |
| 有 DOM/API 的现代网页（如 BOSS直聘） | **中** | 能跑通，但 DOM/API 通常更快、更准、更便宜 |
| 高并发 / 无头服务器 / 规模化采集 | **弱** | 依赖桌面、鼠标、分辨率，难以水平扩展 |

本项目选择 BOSS直聘作为第一个案例，是因为它同时具备“列表 + 详情 + 滚动 + 动态渲染”，适合验证两种模式；**这不代表视觉 RPA 是招聘数据的最优解**。如果要商业化，必须和 DOM/API 方案做成本、准确率、维护成本的对比。

---

## 两种通用模式

代码里的名字保留了历史叫法；在文档里请按“通用语义”理解：

| 模式 | 代码名 | 通用语义 | BOSS 案例中的用法 | 其他领域示例 |
| --- | --- | --- | --- | --- |
| 列表导航模式 | `sms`（历史名“短信模式”） | 在列表区域点击条目，调用一个或多个抄录区域，组装记录 | 点击左侧岗位列表，调用标题/描述区域 | ERP 订单列表、旧版 CRM 客户列表、文件管理器列表 |
| 区域抄录模式 | `transcribe`（“抄录模式”） | 对某个区域截图/OCR，按需滚动，命中停止条件后去噪 | 抄录岗位标题、职位描述 | 发票字段、日志面板、医疗/法务表单、legacy 报表 |

**组合方式**：列表导航模式负责“遍历”，抄录模式负责“读取”；一个列表可以调用多个抄录区域，输出字典模式（一个键区域 + 多个值区域）或集合模式。领域差异主要落在**区域坐标、停止条件、锚点、输出映射**，而不是执行引擎。

> 命名建议（未来代码重构，不在本次文档范围内）：`sms` → `list`，`transcribe` → `region`。当前不改代码，只在文档中明确语义。

---

## 架构

```text
┌──────────────────────────────────────────────────────────────┐
│ 目标 GUI（网页 / 桌面应用 / 遗留系统）                         │
└───────────────────────────────┬──────────────────────────────┘
                                │ 截图 / 点击 / 滚动
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ ScreenDriver（pyautogui / mock）                              │
└───────────────────────────────┬──────────────────────────────┘
                                │ Frame
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ OCR（PaddleOCR / mock） → 文本行 + 置信度 + 坐标                │
└───────────────────────────────┬──────────────────────────────┘
                                │ OcrLine[]
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ Modes：列表导航（sms）/ 区域抄录（transcribe）                  │
│  · 点击、滚动、停止条件、锚点、去重、格式归一                   │
└───────────────────────────────┬──────────────────────────────┘
                                │ 需要语义判断时
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ LLM（DeepSeek / mock）：锚点确认、点击确认、去噪                │
│  · 同步控制流 + 异步去噪 worker（有界队列 + 单一屏障）          │
└───────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ Orchestrator + Storage：JSONL（fsync）/ JSON / 运行报告         │
└──────────────────────────────────────────────────────────────┘
```

详细分层、扩展点和“哪些是领域特定、哪些可复用”见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 一分钟跑通（mock，不移动鼠标）

在**仓库根目录**执行：

```powershell
python -m pip install -r requirements.txt
python run.py demo
```

demo 使用确定性的 mock 屏幕 / mock OCR / mock 大模型，不会移动鼠标。完成后查看终端给出的运行目录：

- `jobs.jsonl`：每行一个岗位（`job_title` / `job_desc` / `captured_at`）；
- `jobs.json`：`{岗位名称: [描述行]}` 汇总；
- `capture_report.json`：计数、停止原因、OCR/LLM 调用次数、告警；
- `run.log`：关键步骤日志。

仓库内附一次 demo 产物：[data/samples/demo_output/jobs.jsonl](data/samples/demo_output/jobs.jsonl)。
脱敏的 schema 示例：[data/samples/sanitized/jobs.jsonl](data/samples/sanitized/jobs.jsonl) / [jobs.json](data/samples/sanitized/jobs.json)。

## 启动前端

```powershell
streamlit run app/streamlit_app.py
```

5 个页签：截图与框选、区域配置、滚动测试、运行采集、结果查看。
第一次使用建议先“载入示例截图 / 示例配置”，确认界面和输出格式，再配置真实页面。

## 真实采集（Windows 桌面）

```powershell
python run.py validate --plan configs/boss_1080p_example.json
$env:DEEPSEEK_API_KEY = "sk-..."
python run.py run --plan configs/boss_1080p_example.json
```

`configs/boss_1080p_example.json` 使用 1920×1080 的 A/B/C 参考坐标。**真实使用前必须按你的屏幕重新框选/校准**；分辨率、DPI、浏览器缩放都会影响坐标。完整步骤见 [docs/RUNBOOK.md](docs/RUNBOOK.md)，当前案例的页面结构和已知问题见 [docs/CASE_BOSS.md](docs/CASE_BOSS.md)。

---

## 验证现状

本地复跑（2026-10-23）：

```text
python -m pytest -q
213 passed, 1 skipped
```

- 真实 OCR 冒烟：`$env:RUN_REAL_OCR="1"; python -m pytest -q tests/test_real_ocr_smoke.py` → 1 passed；
- Streamlit AppTest：空状态/示例截图/示例配置无异常；
- 真实运行：24 个运行目录（含 1 个 demo）与 35 条真实记录；7 次 real + DeepSeek 成功批次；最近连续 5 次成功，19 条记录，0 warning；
- 35 条真实记录描述重复行 0。

**注意**：这些是“工程运行证据”，不是“数据质量验收”。标题/描述质量、20 条稳定性、准确率、成本仍未达标，详见 [docs/EVIDENCE.md](docs/EVIDENCE.md)。

---

## 扩展到其他领域

视觉 RPA 的复用点不在“招聘”，而在“区域 + 模式 + 停止条件 + 输出映射”。新增一个领域，按以下步骤：

1. **定义区域**：列表区、标题区、正文区、按钮区；
2. **选择模式**：列表导航 + 一个或多个区域抄录；
3. **配置行为**：滚动起点/距离、停止标志、锚点、期望格式；
4. **映射输出**：哪个区域做键、哪些区域做值、额外字段；
5. **先 mock 再真实**：用确定性场景验证链路，再做小样本真实运行；
6. **隐私复核**：页面是否含姓名/头像/联系方式/公司敏感信息；是否需要 redaction。

适合迁移的候选：企业内部 ERP/CRM、旧版 Windows 客户端、行业专用软件、没有 API 的报表系统。
不适合直接迁移的候选：有稳定 API/DOM 的现代网页、需要高并发/无头运行的数据采集。

详见 [docs/ROADMAP.md](docs/ROADMAP.md) 和 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 已知限制（诚实清单）

1. **标题/描述质量未达标**：35 条真实记录中，至少 16 条标题有明显薪资混入/截断/OCR 缺字；33 条描述含噪声、PII 或过短；详见证据文档。
2. **锚点失败会终止整批**：首轮 OCR 没识别到“职位描述”时，会交给 LLM 确认；确认失败即整批终止。真实运行中这是最主要的 0 条原因。
3. **停止条件依赖 UI 元素**：C 区域目前主要靠 `去App` 停止；它是页面按钮，不是内容语义边界，存在提前结束的风险。
4. **20 条验收未完成**：当前单次最多 5 条；没有 ground truth、准确率、完整率、单条成本。
5. **隐私边界未工程化**：前端会把真实页面截图写入 `runtime/ui`；`runtime/` 已 gitignore，但本地仍有 PII。不要上传、不要分享。
6. **平台限制**：Windows 桌面、固定分辨率/DPI、鼠标被占用；不适合服务器/无头/多用户。

### 已知启动细节（当前代码未改）

- Streamlit 应用导入 `python-dotenv`，但 `requirements.txt` 当前未列出；若启动报 `ModuleNotFoundError: No module named 'dotenv'`，补装 `python-dotenv`。
- 运行页的“前端窗口标题关键字”需要非空；留空会导致子进程计划校验失败。填一个能匹配窗口标题的词（如 `Streamlit` 或页面标题）即可。
- 这两项属于代码/依赖层问题，已记录在 [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)，本次文档整理不修改代码。

---

## 隐私与合规

- `runtime/` 是本地真实运行产物，已被 `.gitignore` 排除；**绝不要提交或分享**。其中包含真实页面截图、岗位描述、招聘者姓名/状态等。
- API Key 只从环境变量读取；不要把 `.env` 写入仓库或日志。
- 视觉大模型会收到截图/文本；使用前确认目标网站条款、账号权限与数据处理边界。
- 仅在你被授权/允许的范围内使用；不绕过登录、验证码或其他访问控制；不用于商业采集，除非完成法律与平台授权评估。
- 具体的数据分类、脱敏流程、推送前检查见 [docs/PRIVACY.md](docs/PRIVACY.md)。

---

## 目录结构

```text
VibeCoding_Project/
├─ app/streamlit_app.py         # 前端：截图/框选/配置/滚动测试/运行/结果
├─ configs/                     # demo 与 BOSS 示例计划；profiles/ 配置档案
├─ data/samples/                # mock 剧本、截图、OCR 样本、demo 产物
│  └─ sanitized/                # 可上传的脱敏 schema 示例（合成数据）
├─ docs/                        # 架构、证据、案例、隐私、路线图、运行手册
├─ scripts/                     # 一键 demo、滚动测试、截首屏、窗口诊断
├─ src/recruiting_capture/      # 核心包：驱动/OCR/LLM/双模式/编排/存储
├─ tests/                       # 单测 + 契约 + mock 端到端 + 真实 OCR 冒烟
├─ run.py                       # 免安装入口
└─ requirements*.txt
```

## 文档索引

| 文档 | 用途 |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 分层、模式语义、扩展点、领域边界 |
| [docs/EVIDENCE.md](docs/EVIDENCE.md) | 真实运行证据、稳定批次、失败分类、质量审计 |
| [docs/CASE_BOSS.md](docs/CASE_BOSS.md) | BOSS 案例：区域、配置、已知问题、校准步骤 |
| [docs/PRIVACY.md](docs/PRIVACY.md) | 数据分类、脱敏、上传前检查、合规边界 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 从工程样品到多领域 RPA 的路线与非目标 |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | 90 秒/5 分钟演示脚本，面试讲解顺序 |
| [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) | 推送/开源前的细节检查清单 |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | 真实页面运行手册（窗口、坐标、滚动、故障排查） |
| [docs/AMBIGUITIES.md](docs/AMBIGUITIES.md) | 设计冲突、缺失与实现决策 |

---

## 文档状态

本次整理（2026-10-23）只改文档与脱敏样例，未修改任何 `.py` 代码、依赖清单或真实 `runtime/` 数据。
历史交付报告中的旧测试数字/“真实链路待验证”等表述，见 [docs/REPORT.md](docs/REPORT.md) 顶部说明。

## License

当前仓库尚未选择开源许可证。对外分发前，请先确定 License（例如 MIT / Apache-2.0）并补充 `LICENSE` 文件；在此之前，默认保留所有权利。招聘站点的数据使用还需遵守其服务条款与适用法律。
