# 脱敏样例（合成数据）

> 这个目录里的记录**不是真实招聘数据**，是为展示输出 schema 而构造的合成样例。
> 不含真实公司、招聘者、联系方式或页面截图。

## 文件

- `jobs.jsonl`：每行一条记录，字段与真实运行的 `JobRecord` 对齐；
- `jobs.json`：`{条目名称: [描述行]}` 汇总，对应运行报告里的 `jobs.json`；
- 可用于：
  - 演示 JSONL 结构；
  - 写下游解析/分析代码；
  - 作为“可公开的样例”，替代真实 `runtime/runs/*/jobs.jsonl`。

## 字段

| 字段 | 说明 |
| --- | --- |
| `schema_version` | 记录 schema 版本 |
| `job_title` | 条目名称（招聘案例中是岗位名称） |
| `job_desc` | 文本行列表（招聘案例中是职位描述） |
| `captured_at` | 采集时间（ISO 8601，带时区） |
| `company` / `salary` | 可选结构化字段；当前真实运行里常为 `null` |
| `sms_region` | 列表导航区域名（代码历史名 `sms`） |
| `source` | 区域映射：标题区/描述区/数据结构 |
| `warnings` | 该条记录的告警 |

## 为什么不用真实数据

真实 `runtime/` 中包含：

- 招聘者姓名/头像/活跃状态；
- 公司名、地址；
- 页面截图。

这些不适合上传。公开证据使用聚合指标：见 [docs/EVIDENCE.md](../../../docs/EVIDENCE.md)。
脱敏流程与检查表：见 [docs/PRIVACY.md](../../../docs/PRIVACY.md)。

## 使用

```powershell
Get-Content data/samples/sanitized/jobs.jsonl | ForEach-Object { $_ | ConvertFrom-Json }
```

或在 Python 中逐行 `json.loads`。
