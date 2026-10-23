# 智能招聘数据抓取与可视化平台 · 全新原型（VibeCoding_Project）

本项目是依据 `../NEWREADME.MD`（IRDV-DOC-001 v1.0）从零实现的**可运行原型**，不改动原项目任何文件。
它采用“人眼怎么看、人手怎么点”的桌面 GUI 自动化路线：

> 截图裁切 → PaddleOCR 认字 → 视觉大模型确认语义/去噪 → JSONL 增量落盘

技术栈以 NEWREADME 5.1 为准：**Python + Streamlit + streamlit-image-coordinates + pyautogui + PaddleOCR + numpy + DeepSeek 视觉大模型**。

## 一分钟跑通（无需屏幕、网络、API Key）

```powershell
cd VibeCoding_Project
python run.py demo
```

demo 使用确定性的 mock 屏幕/OCR/大模型，**不会移动鼠标**。完成后查看终端给出的运行目录：

- `jobs.jsonl`：每行一个岗位（`job_title` / `job_desc` / `captured_at`）
- `jobs.json`：`{岗位名称: [描述行]}` 汇总
- `capture_report.json`：计数、停止原因、OCR/大模型调用次数、告警
- `run.log`：关键步骤日志
- 若计划配置了 `output.dir`，运行结束还会把本次结果复制为该目录下的 `<run_id>_jobs.jsonl/json`

仓库内已附一次真实 demo 产物：[data/samples/demo_output/jobs.jsonl](data/samples/demo_output/jobs.jsonl)。

## 前端（Streamlit）

```powershell
python -m pip install -r requirements.txt   # 由你安装依赖
streamlit run app/streamlit_app.py
```

页面包含 5 个页签：

1. **截图与框选**：截取首屏（窗口处理方式二选一）/ 载入示例截图；缩放展示 + 半透明选框；拖拽框选区域；两点拾取滚动距离。
2. **区域配置**：按 NEWREADME 5.3.2 配置短信模式/抄录模式全部字段（滚动距离、停止条件、调用区域、集合/字典模式、期望内容/格式、锚点等）。
3. **滚动测试**：FR-04，服务端真实移动/滚动，带倒计时与日志。
4. **运行采集**：校验计划、下载/保存 JSON、启动后台采集进程、查看实时日志与已落盘条数、停止运行。
5. **结果查看**：只读预览（NEWREADME M5 的一小部分，附加项）。

### 截首屏的两种窗口处理方式

| 方式 | 行为 | 适用场景 |
| --- | --- | --- |
| **倒计时模式（默认）** | 不做任何窗口操作；显示倒计时（默认 5 秒，可调 1–60）→ 倒计时结束后固定等待 3 秒页面稳定 → 截图 | 浏览器里打开的前端页面（推荐，绝不会影响命令行/终端） |
| 自动最小化前端窗口 | 只最小化“本服务进程所属、标题匹配”的前端窗口，截图后**后台恢复**（不抢焦点）；定位不到就不做任何最小化 | 独立应用窗口（pywebview/Electron 壳等） |

- 窗口识别可用“API 模式（URL `?page=` 路由参数）”或“窗口标题关键字”；浏览器标签页标题通常只是标签名，因此默认推荐 API 模式或倒计时模式。
- 区域坐标始终是**原始截图**的像素坐标：展示图会等比缩放到一屏内（默认 ≤1280×800，可调），框选时按 `原图坐标 = 显示坐标 × (原图尺寸 / 显示尺寸)` 还原后再写入区域字典。
- 排查窗口时可运行 `python scripts/window_diag.py --monitor 15`（只读，不操作任何窗口）。

> 注意：真实采集时请让目标页面单独可见；运行期间不要触碰鼠标键盘；把鼠标移到屏幕左上角可触发 pyautogui 急停。

## 真实采集

```powershell
python run.py validate --plan configs/boss_1080p_example.json
$env:DEEPSEEK_API_KEY = "sk-..."
python run.py run --plan configs/boss_1080p_example.json
```

`configs/boss_1080p_example.json` 使用 NEWREADME 中 1920×1080 的 A/B/C 参考坐标，**真实使用前请在你自己屏幕上重新框选/校准**（不同分辨率、DPI、浏览器缩放都会影响坐标）。

## 依赖清单

| 依赖 | 版本 | 用途 |
| --- | --- | --- |
| Python | ≥ 3.10 | 运行环境（Streamlit 组件 0.4.x 要求） |
| numpy | ≥1.24 | 截图数组 |
| Pillow | ≥9.0 | 图像保存/叠加/编码 |
| requests | ≥2.28 | DeepSeek HTTP 调用 |
| pyautogui | ≥0.9.54 | 截图、移动、点击、滚动 |
| paddleocr | ≥3.0,<4 | OCR 识别（`PaddleOCR(...).predict()`） |
| paddlepaddle | ≥3.0 | PaddleOCR 运行时 |
| streamlit | ≥1.34 | 前端 |
| streamlit-image-coordinates | ≥0.4.1 | 截图框选组件 |
| pytest（dev） | ≥8 | 测试 |

完整清单见 [requirements.txt](requirements.txt) / [requirements-dev.txt](requirements-dev.txt)。PaddleOCR 首次运行会下载模型；也可提前在有网环境缓存。

## 目录结构

```
VibeCoding_Project/
├─ app/streamlit_app.py         # 前端（截图/框选/配置/滚动测试/运行/结果查看）
├─ configs/                     # demo 与真实页面示例计划
├─ data/samples/                # 截图、OCR 样本、mock 剧本、demo 产物
├─ docs/                        # 报告、设计映射、歧义清单、RUNBOOK、MySQL DDL
├─ scripts/                     # 一键 demo、滚动测试、截首屏、窗口诊断
├─ src/recruiting_capture/      # 核心包（模型/驱动/OCR/LLM/双模式/编排/存储）
├─ tests/                       # 单测 + 端到端 + 真实 OCR 冒烟
├─ run.py                       # 免安装入口
└─ requirements*.txt
```

## 验证现状

本机（开发环境）已完成：

- `python -m pytest -q` → **120 passed, 1 skipped**
- 前端冒烟：用最小 Streamlit 替身执行 `app/streamlit_app.py`，覆盖“载入示例截图 → 缩放展示 → 框选坐标还原”“倒计时模式不碰窗口”“最小化只作用于定位到的窗口”三类场景
- 窗口定位只读验证：`python scripts/window_diag.py`（当前环境无 Streamlit 窗口时返回 None，不会回退最小化其它窗口）
- `python -m pytest -q tests/test_real_ocr_smoke.py`（`RUN_REAL_OCR=1`）→ **1 passed**（真实 PaddleOCR 对静态截图识别，不移动鼠标）
- `python run.py demo` → 3 个岗位、停止原因 `list_end`、无重复行/无界面噪声
- `python run.py validate --plan configs/boss_1080p_example.json` → 通过
- `python -m compileall -q src app scripts tests run.py` → 通过

**未在本机运行**：Streamlit 页面（当前环境未安装 `streamlit` 与 `streamlit-image-coordinates`），以及任何真实鼠标/键盘自动化（避免打扰桌面）。请按 [docs/RUNBOOK.md](docs/RUNBOOK.md) 验证。

## 与 NEWREADME 的差异

设计文档中未定义、互相冲突或明确暂缓的部分，均记录在 [docs/AMBIGUITIES.md](docs/AMBIGUITIES.md)，例如：

- 滚动距离单位（像素差 vs `pyautogui.scroll` 滚轮档位）
- “区域 B2”边界
- 点击确认/50 步回滚（6.2 未包含，8.2 有描述）
- 字典模式多岗位键冲突
- MySQL 入库（FR-09 暂缓）与完整可视化看板（M5）

完整交付说明见 [docs/REPORT.md](docs/REPORT.md)。

## 安全与合规

- 仅用于个人学习/研究，请遵守目标网站使用条款与 robots 约定；
- 采集前请确认账号与数据使用权限，控制采集节奏；
- 不采集与求职分析无关的个人敏感信息；
- API Key 只从环境变量读取，不写入计划文件或日志。
