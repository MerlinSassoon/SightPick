# data/ 样本说明

所有样本都位于 `VibeCoding_Project/` 内，原项目文件只读、未修改。

| 文件 | 来源 / 生成方式 | 用途 |
| --- | --- | --- |
| `samples/screenshot_1080p.png` | 复制自原项目 `screenshot.png`（BOSS直聘 1920×1080 首屏） | 离线框选体验、真实 OCR 冒烟、示例计划坐标依据 |
| `samples/ocr_sample_1080p.json` | 从原项目 `output/screenshot_res.json` 抽取 `rec_texts / rec_scores / rec_boxes` 三个字段 | T2：OCR 结果变换与阈值过滤的离线回放 |
| `samples/prototype_out_dirty.json` | 复制自原项目 `out.json` | 演示原型输出的重复行/招聘者姓名/“去App”等噪声，以及规则清理效果 |
| `samples/demo_story.json` | 为本原型编写的 mock 场景剧本 | mock 驱动的确定性演示：3 岗位 × 3 页描述、重叠行、噪声、停止标志、列表页脚 |
| `samples/overlay_preview.png` | 由 `recruiting_capture.imaging.render_regions` 渲染 | 无需启动 Streamlit 即可查看半透明选框效果 |
| `samples/demo_output/` | `python run.py demo` 的真实产物 | 不运行也能直接检查 JSONL/JSON/报告格式 |

> 说明：`demo_output/capture_report.json` 来源于一次真实 demo 运行；为了作为可移植样本，其中的 `run_dir`/`outputs` 已归一化为样本目录，并移除了指向已清理运行目录的副本路径（文件内 `note` 字段有标注）。

OCR 样本字段说明：

- `item.rec_texts`：识别文本列表；
- `item.rec_scores`：置信度（0~1，含低置信度噪声）；
- `item.rec_boxes`：文本框外接矩形 `[x1, y1, x2, y2]`（原图为 1920×1080）。

重新生成 OCR 样本（仅开发时，需要原项目文件存在）：

```python
import json
from pathlib import Path

src = Path("../output/screenshot_res.json")
data = json.loads(src.read_text(encoding="utf-8"))
payload = {
    "source": "output/screenshot_res.json",
    "screen": {"width": 1920, "height": 1080},
    "item": {
        "rec_texts": data["rec_texts"],
        "rec_scores": data["rec_scores"],
        "rec_boxes": data["rec_boxes"],
    },
}
Path("data/samples/ocr_sample_1080p.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
)
```

重新生成 demo 产物：`python run.py demo`，然后把最新 `runtime/runs/demo_*/` 中的 `jobs.jsonl`、`jobs.json`、`capture_report.json` 复制到 `data/samples/demo_output/`。
