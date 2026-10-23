# 发布检查清单（文档 / 开源 / 作品集）

> 这份清单只做检查，不修代码。
> 目的：把“主体很强、边缘很糙”的项目，变成**面试官 2 分钟能看懂、10 分钟能跑通**的工程样品。

---

## 1. 上传前：隐私与安全（最高优先级）

- [ ] `runtime/` 没有被跟踪：`git check-ignore -v runtime/`
- [ ] `git ls-files` 中没有 `runtime/`、`.env`、真实截图（`*.jpg`/`*.png`）
- [ ] `git grep -n -E 'sk-[A-Za-z0-9]{10,}'` 没有真实 key
- [ ] `.env` 在 `.gitignore` 中；`.env.example` 只有占位符
- [ ] 演示/截图/视频已脱敏：无招聘者姓名、头像、公司名、联系方式
- [ ] 公开的 JSONL 样例是**合成/脱敏数据**，不是真实记录
- [ ] 如果历史里曾提交过敏感数据：先处理历史，再推送；密钥已轮换
- [ ] 详细流程见 [PRIVACY.md](PRIVACY.md)

> `git add -f runtime/` 是禁止操作。

---

## 2. 第一分钟：README

- [ ] 标题能说明“是什么”：视觉 RPA 工程样品，不是“招聘平台”
- [ ] 第一段能说明“为什么值得看”：没有 API/DOM 时，把屏幕变成数据
- [ ] 有 3–5 行“当前状态”：真实链路、测试、限制
- [ ] 有一张架构图或数据流图
- [ ] 有一条**在仓库根目录**可执行的快速开始命令
- [ ] 明确“当前案例是 BOSS，不是系统边界”
- [ ] 明确“不是开箱即用产品 / 不绕过访问控制”
- [ ] 链接到 `docs/EVIDENCE.md`、`docs/ARCHITECTURE.md`、`docs/PRIVACY.md`

---

## 3. 第五分钟：可运行性

- [ ] `python -m pip install -r requirements.txt` 后能启动
- [ ] `python run.py demo` 成功，输出 3 条记录 + 报告
- [ ] `streamlit run app/streamlit_app.py` 能打开页面
- [ ] `python -m pytest -q` 结果与 README 一致
- [ ] 如果 `python-dotenv` 缺失，README 有说明或依赖已补（当前是已知代码问题）
- [ ] 如果窗口标题留空会失败，README 有说明（当前是已知代码问题）
- [ ] 示例配置的坐标有“必须重新校准”的警告

> 当前代码/依赖问题记录在本文末尾“待修代码问题”，本次文档整理不修改代码。

---

## 4. 第十五分钟：证据与限制

- [ ] `docs/EVIDENCE.md` 区分“调试轨迹”和“稳定批次”
- [ ] 不把 24 次运行都说成成功；明确 7 次 real+DeepSeek 成功
- [ ] 写明最大批量 5 条，20 条未完成
- [ ] 写明标题/描述质量指标（16/35、33/35）
- [ ] 写明锚点失败/停止条件/PII 风险
- [ ] 有“当前 vs 目标”的 benchmark 表
- [ ] 没有把 mock 结果外推为真实结果

---

## 5. 开源包装

- [ ] 选择 License（MIT / Apache-2.0 等），补 `LICENSE`
- [ ] 补 `CHANGELOG.md` 或 release notes（可选）
- [ ] 补 `.github/workflows/ci.yml`（pytest + compileall；不跑真实鼠标）
- [ ] 补 issue/PR 模板（可选）
- [ ] 去掉 `.idea/`、`venv/`、`.pytest_cache/` 等 IDE/本地文件
- [ ] 确保 README 的相对链接都能打开
- [ ] 如果有 demo 视频：30–60 秒、脱敏、无 PII

> 当前仓库已有 `.idea/` 被跟踪；建议后续单独清理，不要和文档 PR 混在一起。

---

## 6. 作品集包装

- [ ] 一页 `docs/EVIDENCE.md` 能独立说明“真实跑过”
- [ ] 一页 `docs/ARCHITECTURE.md` 能说明“可迁移”
- [ ] 一页 `docs/CASE_BOSS.md` 能说明“当前案例 + 限制”
- [ ] 一段 90 秒 pitch（见 [DEMO_SCRIPT.md](DEMO_SCRIPT.md)）
- [ ] 一张架构图（Mermaid/ASCII 均可）
- [ ] 一个“已知问题”清单，而不是假装没有
- [ ] 一个“下一步”路线图（见 [ROADMAP.md](ROADMAP.md)）

---

## 7. 本次文档整理改了什么 / 没改什么

**改了：**

- `README.md`：重新定位为视觉 RPA 工程样品；修正路径/测试数字；增加证据、限制、文档索引；
- 新增 `docs/ARCHITECTURE.md`、`docs/EVIDENCE.md`、`docs/CASE_BOSS.md`、`docs/PRIVACY.md`、`docs/ROADMAP.md`、`docs/DEMO_SCRIPT.md`、`docs/RELEASE_CHECKLIST.md`；
- 新增 `data/samples/sanitized/` 合成样例；
- 在旧文档顶部加“历史快照/当前口径”提示（如 `REPORT.md`、`RUNBOOK.md`）。

**没改：**

- 任何 `.py` 代码；
- `requirements*.txt`、`pyproject.toml`、`.gitignore`；
- 真实 `runtime/` 数据；
- 测试/配置逻辑。

---

## 8. 待修代码问题（本次不改，记录给下一轮）

| 优先级 | 问题 | 建议 |
| --- | --- | --- |
| P0 | 标题混入薪资/截断 | B 区域结构化拆分 + 标题硬约束 |
| P0 | 描述噪声/PII | 规则层强制过滤 + redaction |
| P0 | 锚点失败 fatal | 重试/等待/跳过 |
| P1 | `去App` 停止条件 | 改用内容语义/长度/滚动上限 |
| P1 | 默认窗口标题为空导致计划校验失败 | 仅在需要最小化时必填，或给默认值 |
| P1 | `python-dotenv` 未列入依赖 | 补依赖或移除 import |
| P1 | `save_screenshots=false` 仍保留 UI 截图 | 前端截图也纳入开关/保留策略 |
| P2 | 1,722 行 Streamlit 单体 | 拆页面/状态层 |
| P2 | 测试数字/文档路径漂移 | 以 README/CI 为准，统一更新 |
| P2 | 无 License/CI | 发布前补 |

---

## 9. Definition of Done（作品集版）

满足以下条件，可以认为“这个仓库可以拿给面试官看”：

1. 干净环境按 README 能跑通 demo；
2. 有真实运行证据，但全部脱敏；
3. 有架构图 + 领域边界说明；
4. 有明确的已知限制与下一步；
5. 没有 runtime、密钥、真实 PII；
6. 有 License；
7. 面试官能在 5 分钟内回答“这是什么、做到什么程度、下一步做什么”。

---

## 10. 相关文档

- [PRIVACY.md](PRIVACY.md)
- [EVIDENCE.md](EVIDENCE.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [DEMO_SCRIPT.md](DEMO_SCRIPT.md)
- [ROADMAP.md](ROADMAP.md)
