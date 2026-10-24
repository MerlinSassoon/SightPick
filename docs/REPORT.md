# 交付报告 · VibeCoding_Project 全新原型

> **历史快照（2026-10-22）**：本文记录当时的交付状态，其中的测试数字、“真实链路待验证”等表述已被后续真实运行与文档更新取代。
>
> 当前定位、证据与限制请以 [README.md](../README.md)、[EVIDENCE.md](EVIDENCE.md)、[ARCHITECTURE.md](ARCHITECTURE.md) 为准。
>
> 当前测试口径：**214 passed, 1 skipped**（本地 2026-10-24 复跑）；下文 120/131/148 等数字是当时快照。
> 下文历史命令中的 `cd VibeCoding_Project` 按当时工作区书写；当前请在仓库根目录执行 README 中的命令。

> 真源：`../NEWREADME.MD`（IRDV-DOC-001 v1.0，2026-10-22）
> 交付日期：2026-10-22
> 定位：按设计文档从零实现的可运行原型；**未修改原项目任何文件**，所有产物位于 `VibeCoding_Project/`。

## 1. 完成了什么

### 1.1 目标覆盖（NEWREADME 2.3）

| 目标 | 状态 | 实现说明 |
| --- | --- | --- |
| G1 可视化配置 | 完成 | Streamlit 5 页签：截图、拖拽框选（组件缺失时两次点击降级）、半透明选框、区域增删改、两类模式全部配置项、两点测距、滚动测试 |
| G2 自动采集 | 完成（真实链路待现场验证） | 短信模式列表循环 + 抄录模式滚动循环；pyautogui 真实驱动 + PaddleOCR 真实适配器；mock 引擎可离线验证完整链路 |
| G3 语义确认 | 完成 | DeepSeek 视觉接口（OpenAI 兼容，base64 图像）、锚点确认/错字修正、点击确认、结果去噪；失败重试 + 降级 |
| G4 数据可用 | 完成 | 每条岗位即时写 JSONL 并 `flush+fsync`；异常/中断保留已采数据；另出 JSON 汇总与运行报告 |
| G5 可扩展 | 完成 | 区域字典 + 区域配置字典驱动，无 BOSS 坐标/关键字硬编码；停止条件、锚点、格式、映射均可配置 |

### 1.2 功能需求（3.4）

| 编号 | 状态 | 位置 |
| --- | --- | --- |
| FR-01 截首屏供框选 | 完成 | `app/streamlit_app.py`、`cli.py capture-screen`、`capture/window.py` |
| FR-02 多区域 A..Z..A1 | 完成 | `models/region.py` |
| FR-03 模式与参数配置 | 完成 | `models/config.py`、前端“② 区域配置” |
| FR-04 滚动距离测试 | 完成 | `cli.py scroll-test`、`scripts/scroll_test.py`、前端“③ 滚动测试” |
| FR-05 接收字典并实例化 | 完成 | `orchestrator.py`、`engines.py` |
| FR-06 自动点击/滚动/截图/识别 | 完成 | `modes/short_message.py`、`modes/transcribe.py`、`modes/base.py` |
| FR-07 大模型确认/纠错/去噪 | 完成 | `llm/deepseek_client.py`、`llm/prompts.py`、`llm/mock_client.py` |
| FR-08 增量写 JSON/JSONL | 完成 | `storage/writers.py` |
| FR-09 写入 MySQL | **未实现（设计暂缓）** | 仅提供 `docs/schema_mysql.sql` |

### 1.3 设计文档中的已知问题是否修复

- ✅ 硬编码区域/关键字 → 全部改为计划配置；
- ✅ 停止条件精确匹配 → 默认子串（忽略空白）匹配；
- ✅ 输出键错误 → 字典键来自区域 C（可配置 `key_region`）；
- ✅ 重复行 → 滚动重叠行级去重；
- ✅ 噪声 → 视觉大模型去噪 + 规则黑名单（招聘者姓名、“去App”、地址等）；
- ✅ 无增量保存 → 每条岗位立即落盘；
- ✅ 点击确认/回滚 → 可选实现（默认关闭，见 `docs/AMBIGUITIES.md` D2）。

### 1.4 附加内容（明确标注，不属于 NEWREADME 本期范围）

- Mock 屏幕/OCR/大模型与 `data/samples/demo_story.json`：为了无页面、无网络、无 Key 时也能验证全链路；
- 只读“结果查看”页：NEWREADME M5 的一小部分预览，不是完整可视化看板；
- `capture_report.json` 与运行目录约定：满足 NFR-03 可观测性与 NFR-02 数据安全。

## 2. 未完成项及原因

| 未完成项 | 原因 | 后续建议 |
| --- | --- | --- |
| 真实页面连续采集验收（T3/T5/T6/T7、11.2 的 20 份/95%） | 需要你的桌面、登录态、API Key 与目标页面；本代理不操作你的真实鼠标，以免干扰 | 按 `docs/RUNBOOK.md` 第 6–9 节执行 |
| Streamlit 页面运行时验证 | 当前环境未安装 `streamlit` 与 `streamlit-image-coordinates`（按你的要求不下载依赖） | `pip install -r requirements.txt` 后启动验证 |
| 真实 DeepSeek 视觉调用 | 缺少 `DEEPSEEK_API_KEY`；接口已实现，但未发出真实请求 | 配置 Key 后跑 1 个岗位小样本，检查 `llm_calls` 与日志 |
| 完整可视化看板（M5） | NEWREADME 仅列出一句规划，无字段/交互设计，避免臆造 | 以 `jobs.jsonl` 为数据源另行设计 |
| MySQL 入库（FR-09 / 7.4） | 设计明确“本版本暂缓” | `docs/schema_mysql.sql` 已备好，可直接用于后续映射 |
| 公司信息、筛选条件采集（附录 A.1/A.2） | 属于拓展功能区，本期不采集 | 可额外框一个区域，通过 `output.extra_fields` 映射 company/salary |
| 多站点适配（附录 A.3） | 本期以 BOSS直聘 为验证对象 | 配置驱动已具备复用基础 |
| 断点续采 | NEWREADME 未设计；当前保证“已采数据不丢”，但不自动续跑 | 可基于 `jobs.jsonl` 做去重后续采 |
| 加权滚动距离自动应用 | 公式未定义且单位不确定（D10） | 默认只记录建议值，验证后再开启 `auto_adjust_scroll` |
| 点击确认默认启用 | 6.2 与 8.2 冲突（D2），且会显著增加调用成本 | 现场需要时在区域配置中开启 |
| 真实 OCR/截图在本机运行 | 只做了静态截图 OCR 与 pyautogui 截图能力测试，未执行移动/点击 | 真实执行需你操作 |

## 3. 本机验证记录

环境：Windows，Python 3.11.4；numpy 2.3.2、Pillow 11.3.0、pytest 9.0.1、pyautogui 0.9.54、paddleocr 3.7.0、paddlepaddle 3.2.0。

| 验证 | 命令 | 结果 |
| --- | --- | --- |
| 全量单测/端到端 | `python -m pytest -q` | **120 passed, 1 skipped**（skip 为真实 OCR，默认关闭） |
| 前端空状态 | `python -m pytest -q tests/test_streamlit_app_smoke.py` | 通过（最小 Streamlit 替身执行 app；非真实页面渲染） |
| 前端截屏/框选流程 | `python -m pytest -q tests/test_streamlit_app_capture_flow.py` | 通过：缩放展示→框选坐标精确还原；倒计时模式零窗口操作；最小化只作用于定位到的窗口，定位失败不碰任何窗口 |
| 窗口定位策略 | `python -m pytest -q tests/test_window_targets.py` | 通过：绝不选中 PowerShell/命令提示符，路由参数优先，找不到返回 None |
| 窗口只读诊断 | `python scripts/window_diag.py --monitor 15` | 通过：本机无 Streamlit 窗口时返回 None，前台窗口时间线无变化 |
| 真实 PaddleOCR 冒烟 | `$env:RUN_REAL_OCR="1"; python -m pytest -q tests/test_real_ocr_smoke.py` | **1 passed**（对 `data/samples/screenshot_1080p.png` 的 C 区域静态识别，20.06s） |
| mock 全链路 | `python run.py demo` | **3 个岗位**；停止原因 `list_end`；描述行数 17/17/18；无重复行、无界面噪声 |
| CLI 覆盖参数 | `python run.py run --plan configs/boss_1080p_example.json --engine mock --llm mock --max-jobs 2 --run-dir runtime/runs/cli_smoke` | 2 个岗位；`stop_reason=max_jobs_reached`；输出副本写入 `runtime/output/` |
| demo 指标 | 读取 `capture_report.json` | 16 次截图/OCR、3 次点击、9 次滚动、6 次大模型调用、0 warning、耗时 0.45s |
| 示例计划校验 | `python run.py validate --plan configs/boss_1080p_example.json` | 通过 |
| 语法编译 | `python -m compileall -q src app scripts tests run.py` | 通过 |
| 选框渲染 | 生成 `data/samples/overlay_preview.png` 并目视核对 | 三个半透明区域位置与参考截图一致 |

demo 的真实产物已保存到 [`data/samples/demo_output/`](../data/samples/demo_output/)：`jobs.jsonl`、`jobs.json`、`capture_report.json`。
计划中的 `output.dir` 会在运行结束时收到本次结果的副本（`<run_id>_jobs.jsonl/json`），用于跨运行归档；权威数据仍以运行目录为准。

## 4. 如何运行验证（最短路径）

### 4.1 离线演示（推荐先做，不需要安装任何新依赖，也不会动鼠标）

```powershell
cd VibeCoding_Project
python run.py demo
```

预期：终端显示 3 个岗位与运行目录；检查 `jobs.jsonl` 每行一个 JSON，`job_desc` 无重复、无“去App/示例招聘者/工作地址”。

### 4.2 前端界面（需安装 Streamlit 栈）

```powershell
python -m pip install -r requirements.txt
streamlit run app/streamlit_app.py
```

按“① 截图与框选 → ② 区域配置 → ③ 滚动测试 → ④ 运行采集 → ⑤ 结果查看”顺序操作。可先点“载入示例截图”离线体验框选；运行采集时引擎选 `mock`，无需 API Key。

### 4.3 真实采集

```powershell
python run.py validate --plan configs/boss_1080p_example.json
$env:DEEPSEEK_API_KEY = "sk-..."
python run.py run --plan configs/boss_1080p_example.json
```

完整步骤、坐标参考、滚动校准、异常恢复与验收表见 [`docs/RUNBOOK.md`](RUNBOOK.md)。

## 5. 设计偏差摘要

设计文档中存在 22 处缺口/冲突与 7 处实现配套新增，全部记录在 [`docs/AMBIGUITIES.md`](AMBIGUITIES.md)，其中需要你最终确认的关键项：

1. **滚动距离单位**：原型按 `pyautogui.scroll` 滚轮档位处理，不是像素；请用滚动测试校准。
2. **B2 边界**：设计未定义，原型每轮重裁完整 B 区域并做行级去重。
3. **点击确认/50 步回滚**：6.2 类规格未包含，原型做成可选能力，默认关闭。
4. **锚点加权滚动距离**：无公式，原型只给建议值，不自动覆盖用户配置。
5. **字典模式重复键**：默认合并描述；JSONL 仍逐条保留。
6. **Python 版本**：因 Streamlit 组件要求，提高到 3.10+。
7. **前端最小化**：按窗口标题匹配；同浏览器窗口场景需关闭该选项。

## 6. 已知风险

- **DPI/分辨率**：坐标与屏幕绑定；运行前有校验，不一致默认中止。
- **页面改版**：需重新框选与调整停止标志（配置驱动已降低维护成本）。
- **OCR 漏识/错字**：置信度阈值 + 大模型兜底 + 规则降级；仍建议人工抽检。
- **DeepSeek 不稳定/成本**：有重试与降级；默认只在每份岗位结束时去噪一次。
- **自动化合规**：仅在授权、合规前提下使用；控制节奏，不绕过访问控制。

## 7. 文件导航

- 使用说明与依赖：[`README.md`](../README.md)
- 设计映射：[`DESIGN_MAPPING.md`](DESIGN_MAPPING.md)
- 冲突/缺失与假设：[`AMBIGUITIES.md`](AMBIGUITIES.md)
- 真实页面手册：[`RUNBOOK.md`](RUNBOOK.md)
- MySQL 规划 DDL：[`schema_mysql.sql`](schema_mysql.sql)
- 核心实现：[`src/recruiting_capture/`](../src/recruiting_capture/)
- 前端：[`app/streamlit_app.py`](../app/streamlit_app.py)
- 测试：[`tests/`](../tests/)
- 样例：[`data/samples/`](../data/samples/)
