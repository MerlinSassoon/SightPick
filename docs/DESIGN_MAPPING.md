# NEWREADME 设计条目 → 实现映射

> 真源：`../NEWREADME.MD`（IRDV-DOC-001 v1.0）。下表用于评审时快速定位实现位置。

| NEWREADME 位置 | 设计要点 | 实现位置 |
| --- | --- | --- |
| 5.1 技术栈 | Streamlit + `streamlit-image-coordinates` 拖拽框选、半透明选框 | `app/streamlit_app.py`；`src/recruiting_capture/imaging.py`；`requirements.txt` |
| 5.2 交互流程 | 截首屏 → 框选 → 配置 → 滚动测试 → 前端最小化/启动 | `app/streamlit_app.py`（①③④ 页签）；`capture/window.py`；`cli.py::cmd_capture_screen` |
| 5.3.1 区域字典 | 键 A..Z、A1…；值 `[xmin,ymin,xmax,ymax]` | `models/region.py`（`Region`、`region_name`、`next_region_name`） |
| 5.3.2 区域配置字典 | 短信/抄录两组配置项 | `models/config.py`（`SmsConfig`、`TranscribeConfig`） |
| 5.3.3 文本数据结构 | 集合模式 / 字典模式 | `modes/short_message.py`（`_assemble`、`_flatten_values`） |
| 6.1 公共基础能力 | 移动/滚动/点击/截图裁切/坐标变换/结果变换 | `modes/base.py`；`capture/driver.py`；`capture/pyautogui_driver.py`；`ocr/engine.py` |
| 6.2 短信模式类 | 构造配置、绑定抄录、调用、工作流程 | `modes/short_message.py` |
| 6.3 抄录模式类 | 可滚动/不可滚动、去噪、工作流程 | `modes/transcribe.py` |
| 6.4 主程序流程 | 初始化 → 前端 → 接收配置 → 实例化 → 执行 → 落盘 → 终止 | `orchestrator.py::run_plan`；`engines.py`；`cli.py` |
| 6.5.1 截图裁切与初步分析 | `predict()[0]`、`rec_texts/rec_scores/rec_boxes`、阈值 0.8 | `ocr/engine.py::transform_predict_result`；`ocr/paddle_engine.py`；`config.py` 中的 `ocr_confidence_threshold` |
| 6.5.2 滚动距离 | 实时测试程序 | `cli.py::cmd_scroll_test`；`scripts/scroll_test.py` |
| 6.5.3 首轮无“职位描述”键容错 | 截图+字典交大模型；错字修正/报错；加权滚动 | `modes/transcribe.py::_ensure_anchor`、`_maybe_adjust_scroll` |
| 6.6 状态推进与终止 | 份数上限 / 列表上限 / 大模型失败 | `orchestrator.py`；`modes/short_message.py`；`modes/transcribe.py` |
| 7.1 运行时字典 | 初步分析缓存、截图缓存、描述列表、字典 A、岗位字典 | `Frame`、`OcrLine`、`SmsMode._items`、`SmsMode._assemble`、`storage.build_jobs_archive` |
| 7.2 PaddleOCR 输出字段 | 实测字段与阈值必要性 | `ocr/engine.py`；样本 `data/samples/ocr_sample_1080p.json` |
| 7.3.1 JSON 输出 | `{岗位名称: [描述行]}` | `storage/writers.py::build_jobs_archive` → `jobs.json` |
| 7.3.2 JSONL 输出 | 每条岗位一行、含时间戳 | `storage/writers.py::JobRecord` / `JsonlWriter` → `jobs.jsonl` |
| 7.4 MySQL 存储（规划） | 两张表与唯一约束 | 仅 `docs/schema_mysql.sql`（未接入运行时） |
| 8.1 主流程时序 | 截图/确认/落盘时序 | `orchestrator.py` 日志与流程；`run.log` |
| 8.2 左侧处理流程 | 点击循环、点击确认、50 步回滚 | `modes/short_message.py::workflow`、`_perform_click`、`_rollback`（`verify_click` 可选） |
| 8.3 右侧处理流程 | C 事实确认、B 锚点检查、滚动、去噪 | `modes/transcribe.py`；`llm/prompts.py` |
| 8.4 异常处理 | 关键字缺失、点击未命中、接口失败、中断 | `errors.py`；`orchestrator.py`；`modes/*`；`storage/writers.py` |
| 9.1–9.4 现状与问题 | 硬编码、滚动参数、停止判定、重复、键错误、噪声、无增量 | 全部以配置驱动重写；`text_utils.py`；`short_message.to_records`；`JsonlWriter` |
| 10 风险 | DPI、OCR 错字、页面改版、限流、误触、接口、格式、重复 | `ScreenGeometry` + 屏幕校验、置信度阈值、配置驱动、随机动作间隔、急停、重试/降级、统一 JSONL、去重 |
| 11 测试与验收 | T1–T8、验收标准 | `tests/`；`RUNBOOK.md` 手工验收步骤 |
| 12 运行与使用 | 依赖、步骤 | `README.md`；`RUNBOOK.md`；`requirements.txt` |
| 13 / 附录 A | M1–M6、拓展功能 | M1–M4 已实现；M5 仅只读预览；M6/附录 A 未实现（见 `AMBIGUITIES.md`） |
