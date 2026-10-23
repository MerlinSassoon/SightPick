# 隐私、数据与合规边界

> 这份文档不是法律意见，而是一份工程检查表。
> 目标：**让真实运行数据留在本地，让可公开的内容不泄露个人信息。**

---

## 1. 这个项目会产生哪些数据

| 数据 | 位置（本地） | 是否含个人信息 | 风险 |
| --- | --- | --- | --- |
| 真实页面截图 | `runtime/ui/view_*.jpg`、`runtime/ui/first_screen.png` | **是**：招聘者姓名/头像、公司、岗位信息 | 高 |
| 展示用压缩图 | `runtime/ui/*_display.jpg` | 可能是 | 中 |
| 原始 OCR / 描述 | `runtime/runs/*/jobs.jsonl`、`raw/` | **可能**：姓名、公司、地址、联系方式 | 高 |
| 运行日志 | `runtime/runs/*/run.log`、`console.log` | 可能含页面路径/岗位标题 | 中 |
| 计划文件 | `runtime/plans/*.json` | 通常不含人名；含坐标/标题关键字 | 低 |
| API Key | `.env` 或环境变量 | **是**：密钥 | 高 |
| 代码/文档 | 仓库 | 不应含真实数据 | 低 |

**结论**：`runtime/` 是本地隐私数据，不是普通构建产物；`.gitignore` 只是最后一道门，不是唯一防线。

---

## 2. 当前已知的隐私细节

- 前端截图/展示流程会把真实页面图写入 `runtime/ui`；
- `run.save_screenshots=false` 只控制模式层的调试裁切图，**不控制前端首屏/展示图**；
- 本地运行集合中已有 **107 张 `view_*.jpg` + `first_screen.png`**；
- 35 条真实记录中，规则审计发现 **12 行**含招聘者姓名/“招聘者”/“刚刚活跃”等信息；
- 部分描述末尾含公司名/地址；
- 大模型在锚点/点击确认时会收到截图，在去噪时会收到文本。

这些是工程现状，不代表可以公开。

### 2.1 仓库中已有的示意样本

`data/samples/` 里还有早期开发用的 `screenshot_1080p.png`、`ocr_sample_1080p.json`、`prototype_out_dirty.json`、`demo_story.json`。它们可能包含示例姓名/公司名，但不是 `runtime/` 真实运行数据。
公开分发前建议替换为合成夹具；替换会涉及测试断言，属于后续代码/测试改动，不在本次文档整理范围内。新增的 `data/samples/sanitized/` 是纯合成数据。

---

## 3. 绝对不要上传的内容

- `runtime/` 整个目录；
- `runtime/ui/` 下任何真实页面截图；
- `runtime/runs/` 下的 `jobs.jsonl`、`raw/`、`run.log`；
- `.env`、任何 `sk-...` 密钥；
- 含真实公司名/招聘者姓名/联系方式的样例、截图、演示视频；
- 浏览器 profile、cookie、登录态导出。

**也不要**用 `git add -f` 绕过 `.gitignore`。

---

## 4. 上传前的检查（Windows / PowerShell）

在仓库根目录执行：

```powershell
# 1) runtime 是否被忽略？
git check-ignore -v runtime/

# 2) 工作区有没有 runtime / .env / 截图被跟踪？
git status --short
git ls-files | Select-String -Pattern 'runtime/|\.env$|\.jpg$|\.png$'

# 3) 有没有明显的 key/token？
git grep -n -E 'sk-[A-Za-z0-9]{10,}|DEEPSEEK_API_KEY\s*=\s*[^\s]' -- .

# 4) 最近一次提交里有没有大文件/图片？
git show --stat --oneline HEAD
```

期望：

- `git check-ignore` 输出 `runtime/`；
- `git ls-files` 不出现 `runtime/`、`.env`、真实截图；
- `git grep` 不出现真实 key；
- 提交里只有代码/文档/脱敏样例。

> 如果 `runtime/` 已被跟踪，不要直接 `git rm --cached` 后继续提交；先确认历史里是否已经包含敏感数据，再决定是否重写历史。涉及已推送的密钥，应立即轮换。

---

## 5. 脱敏流程（文本）

对要公开的岗位记录：

1. **删除/替换人名**：招聘者姓名、联系人、HR 姓名；
2. **删除/替换状态**：`刚刚活跃`、`在线`、`今日活跃`；
3. **删除/替换公司名**：如必须保留行业，用“某电商公司”替代；
4. **删除地址/联系方式**：精确地址、电话、邮箱、微信；
5. **删除截图中的头像/姓名/公司 logo**；
6. **保留结构**：岗位类型、技能标签、描述段落结构可以保留；
7. **标注来源**：写明“合成/脱敏示例，非真实招聘数据”。

推荐公开的样例格式：

```json
{
  "schema_version": "1.0",
  "job_title": "示例岗位：视觉 RPA 工程师",
  "job_desc": ["示例职责 1", "示例职责 2"],
  "captured_at": "2026-01-01T00:00:00+08:00",
  "company": "示例公司",
  "salary": "20-30K",
  "sms_region": "A",
  "source": {"title_region": "B", "desc_region": "C", "data_structure": "dict"},
  "warnings": []
}
```

仓库里的 [data/samples/sanitized/jobs.jsonl](../data/samples/sanitized/jobs.jsonl) 就是这种**合成示例**，不是真实数据。

---

## 6. 脱敏流程（图片/视频）

如果一定要展示界面：

- 裁掉招聘者卡片、头像、姓名、公司 logo；
- 或对姓名/头像/公司名做马赛克/模糊；
- 不要在视频里出现浏览器账号、通知、书签、桌面文件名；
- 不要展示 `runtime/` 路径、API Key、终端历史；
- 优先用 mock 界面/示意图代替真实截图。

**最稳妥的做法**：不展示真实页面截图，只展示架构图 + 脱敏 JSONL + 聚合指标。

---

## 7. 第三方大模型

- 锚点/点击确认会把截图发给模型；
- 去噪会把 OCR 文本发给模型；
- 发送前尽量裁剪到目标区域，减少无关个人信息；
- 不要把 API Key 写入计划、日志、截图；
- 使用前确认模型服务商的数据保留/训练政策；
- 如果数据敏感，考虑本地模型或先做 redaction。

---

## 8. 合规边界

- 仅使用你有权访问、且平台条款允许的数据；
- 不绕过登录、验证码、付费墙或其他访问控制；
- 不用于商业采集/转售，除非完成平台授权与法律评估；
- 控制频率，避免对目标站点造成压力；
- 遵守个人信息保护相关法律（如适用）；
- 本文档不构成法律意见；商业化前请咨询专业顾问。

---

## 9. 如果发生泄露

1. 立即停止上传/分享；
2. 轮换 `DEEPSEEK_API_KEY` 及任何相关密钥；
3. 删除公开仓库中的敏感文件；
4. 如果已经推送到 Git 历史，使用 `git filter-repo` / BFG 重写历史，并强制推送；
5. 评估是否需要通知受影响方；
6. 把事故原因写进 `docs/`，避免再次发生。

---

## 10. 相关文档

- [EVIDENCE.md](EVIDENCE.md)：可公开的聚合证据
- [CASE_BOSS.md](CASE_BOSS.md)：案例中的 PII 风险点
- [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)：推送前检查清单
- [../README.md](../README.md)：项目入口
