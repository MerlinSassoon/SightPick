# 大模型异步化重构执行手册

| 文档属性 | 内容 |
| --- | --- |
| 文档编号 | IRDV-RF-001 |
| 版本 | v1.0（已评审，可直接执行） |
| 适用仓库 | `VibeCoding_Project/` |
| 真源设计 | `../NEWREADME.MD`（IRDV-DOC-001） |
| 执行者 | 负责重构的 AI 编码代理 |
| 编制日期 | 2026-10-23 |

---

## 0. 给执行者的话（先读这一节，再读其余）

### 0.1 你的职责边界

你要完成的是**一次受控的异步化改造**，不是重写。行为不变量（第 2 章）不可协商；任何为了提高“优雅度”而改变对外行为、文件格式、日志措辞或事件顺序的改动，**一律不要做**。

### 0.2 全局克制规则（每一步都适用）

| 编号 | 规则 | 说明 |
| --- | --- | --- |
| R-01 | **不做顺手重构** | 看到相邻代码风格不一致、命名不优雅、注释过时，记录到本步骤的“已知问题”里，不要改。 |
| R-02 | **不扩大单步范围** | 每步只改“本步骤改动清单”列出的文件与行区间。需要改清单外文件 = 步骤设计有误，停下来报告。 |
| R-03 | **优先保持签名兼容** | 新参数必须带默认值；默认值必须等价于旧行为。 |
| R-04 | **测试即契约** | 现有测试通过即为行为不变的最低证据。若某测试必须修改，**只能在明确标注的地方修改**，且必须解释原因。 |
| R-05 | **先读后改** | 修改任何文件前，必须先用 read 工具读完整函数（不是只读片段）。 |
| R-06 | **不新增依赖** | 只用标准库 `threading` / `queue` / `contextlib`。不引入 anyio、concurrent.futures 之外的异步框架。 |
| R-07 | **不写提前量** | 不实现“以后可能会用”的参数、抽象层、基类、插件点。 |
| R-08 | **异常不许静默** | 新增的 `except` 必须至少有 log 或转为 warning，禁止裸 `pass`（既有代码的 `pass` 不动）。 |
| R-09 | **文档同步** | 涉及设计决策的改动，同一步内更新 `docs/AMBIGUITIES.md`；涉及行为的，更新 `README.md` 对应句子。 |
| R-10 | **一步一提交** | 每步结束跑验收命令并记录结果，再进入下一步。**不要跨步骤批量改**。 |

### 0.3 每步的标准开工/收工流程

```
开工：读“本步骤必读代码” → 确认改动清单 → 改动
收工：跑“验收命令” → 比对“完成判定” → 记录到第 8 章执行日志
        → 未达判定 = 回退本步骤改动，报告失败原因
```

### 0.4 验收基线（改动前必须先确认）

在**动任何代码之前**，先跑一次并记录结果，作为后续每一步的对照基线：

```powershell
cd VibeCoding_Project
python -m pytest -q
```

预期基线：**120 passed, 1 skipped**（与 `README.md` “验证现状”一致）。

如果基线不是这个数，**先停下来报告**，不要开始 P0。

---

## 1. 目标与已定决策

### 1.1 一句话目标

把 `LLMClient.denoise` 从“主线程同步阻塞调用”改为“提交到后台线程池 + 返回占位符，主线程在短信模式汇总前统一结算”。

### 1.2 已确定的产品决策（不可再讨论）

| 编号 | 决策 | 影响 |
| --- | --- | --- |
| C-1 | 工作线程数默认 **2** | `llm_worker_threads=2`，可退回 1 |
| C-2 | `verify_click` / `confirm_anchor` **保持同步**，绝不进线程池 | 控制流依赖，异步化会破坏异常语义 |
| C-3 | 落盘走 **方案 A**（sms 级，不逐项落盘） | 不改 `orchestrator` 的落盘粒度 |
| C-4 | 重试 = 底层 **1** 次 + 外层 **1** 次 | `llm_max_retries=1`，`llm_task_retries=1` |
| C-5 | **不实现** `denoise_per_round`，并移除其界面控件 | 见步骤 P4 |
| C-6 | 抄录模式只发**初步分析文本**；短信模式只发**图片** | 见步骤 P3 的 `send_frame` |
| C-7 | **不做** `on_item` 逐项落盘 | 等后续讨论 |
| C-8 | 不实现 `llm_frame_policy` 这类“策略开关” | C-6 已用二值规则替代 |

### 1.3 本次明确不做

- 不改 JSONL / jobs.json / capture_report 的**既有字段语义**（只新增字段）；
- 不改 `MockScreenDriver` / `MockOcrEngine` 的剧本格式；
- 不改前端除 C-5 之外的任何交互；
- 不改 `docs/schema_mysql.sql`；
- 不为 `denoise` 增加流式、缓存、批量合并等优化。

---

## 2. 行为不变量（每一步都必须成立）

违反任一条即判定该步骤失败，**回退**。

| 编号 | 不变量 | 如何验证 |
| --- | --- | --- |
| I-1 | `TranscribeMode.workflow()` 无参调用时行为与改造前**完全一致** | `test_modes.py` 全绿 |
| I-2 | `ShortMessageMode.workflow()` 返回值**不含** `PendingText` 或任何内部句柄 | 步骤 P2 的 A1 测试 |
| I-3 | 同一计划分别在 `llm_async=false` / `true` 下运行，`jobs.jsonl` **逐字节相同** | 步骤 P3 的 A2 对拍测试 |
| I-4 | 岗位顺序、描述行顺序、集合模式列表顺序与改造前一致 | `test_orchestrator_demo.py` 第 39-43 行的精确断言 |
| I-5 | 第 N 个岗位的失败告警**不会**出现在第 M 个岗位（M≠N） | 步骤 P2 的 A3 测试 |
| I-6 | 大模型失败时仍按 R6 降级为规则清理，且 `job_desc` **不为空** | 既有 `test_llm_failure_degrades_to_rules` |
| I-7 | 主干流程中**没有任何** GUI 操作在非主线程执行 | 代码审查 + 步骤 P2 的 W 系列 |
| I-8 | `capture_report.json` 的既有字段全部保留，`llm_calls > 0` | `test_orchestrator_demo.py` 第 62 行 |

> **I-3 是本项目唯一的语义等价性证明**。如果它通过，其余不变量基本自动成立。

---

## 3. 总体结构（理解用，不是改动清单）

```
主线程（唯一能碰 GUI 的线程）
└─ run_plan()                                   orchestrator.py
   └─ ShortMessageMode.workflow()                short_message.py
      ├─ _collect()  ← 点击/滚动/截图/OCR        （原 while 循环体）
      │  └─ TranscribeMode.workflow(worker)      transcribe.py
      │     ├─ 同步段：截图 + OCR + 锚点确认（控制流，主线程）
      │     └─ 末尾：worker.submit(...) → PendingText   ← 立即返回
      ├─ _resolve_all_pending()   ← 唯一屏障，按 items 顺序
      ├─ _assign_titles()         ← 解析后再做键（原在 _invoke_transcribe_modes）
      └─ _assemble()

后台线程（默认 2 个，daemon）
└─ LLMWorker._run()                              llm/worker.py
   ├─ queue.get() → DenoiseTask（不可变快照）
   ├─ denoise_once(worker_client, ...)            modes/denoise_ops.py
   └─ handle.fulfill(...)  ← 幂等写入
```

**为什么屏障在 `_assemble` 之前**：它是全流程唯一同时需要所有区域最终值的地方，且天然按 `self.items` 顺序遍历——顺序即确定性。屏障在 `workflow()` 内部，因此 orchestrator、`to_records`、前端全部零改动。

---

## 4. 步骤总览

| 步骤 | 名称 | 改动性质 | 独立可验收 | 预计规模 |
| --- | --- | --- | --- | --- |
| **P0** | 纯函数下沉 + 异常类型上移 | 无行为变化的重构 | pytest 120 passed 不变 | +5 / ±30 行 |
| **P1** | `LLMWorker` + `PendingText` | 纯新增 | 新增单测全绿 | +180 / +120 行 |
| **P2** | 两模式接入 worker 与屏障 | 行为等价改造 | 对拍 + A 系列 | ±90 行 |
| **P3** | 配置项、工厂注入、报告聚合 | 新增配置与统计 | 全量 + A2 | ±60 行 |
| **P4** | 移除 `denoise_per_round` | 删除功能 | 全量 + 前端冒烟 | ±40 行 |

P0、P1 **不动现有行为**，可以先做、可单独回退。P4 与前三步无依赖，可与 P0 并行，但**必须在 P3 之后合并**（因为 P3 会改同一批配置文件与 `RunConfig`，避免冲突）。

---

# 步骤 P0：纯函数下沉 + 异常类型上移

## P0.1 目标

把 `TranscribeMode` 的三个私有方法改造成**无状态纯函数**，使 worker 能在不引用模式实例的前提下复用同一套去噪逻辑。**此步骤不改变任何对外行为**。

## P0.2 为什么必须单独一步

如果直接在 P2 里让 worker 调用 `mode._denoise()`，worker 会持有模式实例引用，从而读取 `self.config`、`self.warnings`——而那些状态在每次 `workflow()` 开头会被重置（[transcribe.py](VibeCoding_Project/src/recruiting_capture/modes/transcribe.py) 第 40-58 行）。这是“延迟回调读到可变实例状态”这类最难排查的 bug 的温床。先把逻辑抽成纯函数，**从结构上消灭这种可能性**。

## P0.3 必读代码

| 顺序 | 文件 | 读什么 | 为什么 |
| --- | --- | --- | --- |
| 1 | [modes/transcribe.py](VibeCoding_Project/src/recruiting_capture/modes/transcribe.py) | 第 200-255 行（`_denoise` / `_rule_based_result` / `_apply_format`） | 这就是要下沉的全部逻辑 |
| 2 | [text_utils.py](VibeCoding_Project/src/recruiting_capture/text_utils.py) | `dedupe_lines` / `clean_lines` / `clean_title` / `pick_title_candidate` | 纯函数依赖的工具函数，确认它们无副作用 |
| 3 | [models/config.py](VibeCoding_Project/src/recruiting_capture/models/config.py) | 第 17-20 行（`FORMAT_STR` / `FORMAT_LIST` / `FORMAT_TUPLE`） | 格式常量 |
| 4 | [errors.py](VibeCoding_Project/src/recruiting_capture/errors.py) | 全文（35 行） | 异常继承关系 |
| 5 | [llm/deepseek_client.py](VibeCoding_Project/src/recruiting_capture/llm/deepseek_client.py) | 第 37-39 行（`_NonRetryableLLMError`） | 要上移的私有类 |
| 6 | [llm/client.py](VibeCoding_Project/src/recruiting_capture/llm/client.py) | 全文（58 行） | `LLMClient` 协议，确认 `denoise` 签名 |
| 7 | [tests/test_modes.py](VibeCoding_Project/tests/test_modes.py) | 第 101-111 行 | `test_llm_failure_degrades_to_rules`，它断言 `mode.warnings` 里含“降级”，决定了薄委托必须保留 |

**不要读**：`orchestrator.py`、`app/streamlit_app.py`、`capture/*`。本步骤与它们无关，读了只会诱导你扩大范围。

## P0.4 改动清单

### (a) `errors.py`：新增公开异常

在 `LLMError` 之后插入：

```python
class NonRetryableLLMError(LLMError):
    """重试无意义的失败（如 4xx 非 429）。

    客户端与异步 worker 据此区分“可重试的瞬时故障”与“重试也必然失败”。
    """
```

同时在 `deepseek_client.py` 中把 `_NonRetryableLLMError` **改为别名**，保持向后兼容：

```python
from ..errors import LLMError, NonRetryableLLMError

_NonRetryableLLMError = NonRetryableLLMError  # 兼容旧名；新代码请直接用公开名
```

> 保留旧名别名是 R-03 的落实——任何外部 `except _NonRetryableLLMError` 都不会失效。

### (b) 新建 `src/recruiting_capture/modes/denoise_ops.py`

三个函数，**零副作用、不读任何 self 状态、不抛异常**：

```python
"""结果去噪的纯函数实现（NEWREADME 6.3 函数八点零）。

同步路径（TranscribeMode）与异步路径（LLMWorker）共用这里的唯一实现，
避免两份逻辑漂移。所有函数不得读取任何实例状态。
"""

from __future__ import annotations

from typing import Any

from ..errors import LLMError
from ..llm.client import LLMClient
from ..capture.driver import Frame
from ..models.config import FORMAT_STR, FORMAT_TUPLE
from ..text_utils import clean_lines, clean_title, dedupe_lines, pick_title_candidate


def rule_based_result(texts: list[str], expected_format: str) -> str | list[str]:
    """R6 降级路径：不调用大模型，仅按规则清理。"""
    cleaned = clean_lines(texts, drop_noise=True)
    if expected_format == FORMAT_STR:
        return clean_title(pick_title_candidate(cleaned))
    return cleaned


def apply_expected_format(
    result: str | list[str] | tuple[str, ...],
    fallback_texts: list[str],
    expected_format: str,
) -> str | list[str] | tuple[str, ...]:
    """把大模型返回值规整到配置声明的格式。"""
    if expected_format == FORMAT_STR:
        if isinstance(result, str):
            title = clean_title(result)
        else:
            title = clean_title(pick_title_candidate([str(item) for item in result]))
        if title:
            return title
        return clean_title(pick_title_candidate(fallback_texts))
    lines = [result] if isinstance(result, str) else [str(item) for item in result]
    lines = dedupe_lines(line for line in lines if line.strip())
    if expected_format == FORMAT_TUPLE:
        return tuple(lines)
    return lines


def denoise_once(
    llm: LLMClient,
    *,
    texts: list[str],
    frame: Frame | None,
    expected_content: str,
    expected_format: str,
) -> tuple[Any, list[str]]:
    """同步与异步共用的唯一去噪实现。

    返回 ``(已套用期望格式的结果, 告警列表)``；**永不抛异常**。
    失败时按 R6 降级为规则清理，并返回降级原因。
    """

    try:
        raw = llm.denoise(
            texts=texts,
            frame=frame,
            expected_content=expected_content,
            expected_format=expected_format,
        )
    except LLMError as exc:
        return (
            rule_based_result(texts, expected_format),
            [f"去噪调用大模型失败，按 R6 降级为规则清理：{exc}"],
        )
    return apply_expected_format(raw, texts, expected_format), []
```

> **注意**：`denoise_once` 只捕获 `LLMError`。其他异常（如 `KeyError`）应当**向上冒泡**，由调用方（transcribe 的既有 try 或 worker 的兜底）处理。不要扩大 `except` 范围。

### (c) `transcribe.py`：三个方法改为薄委托

```python
from .denoise_ops import apply_expected_format, denoise_once, rule_based_result

    def _rule_based_result(self, texts: list[str]) -> str | list[str]:
        return rule_based_result(texts, self.config.expected_format)

    def _apply_format(
        self,
        result: str | list[str] | tuple[str, ...],
        fallback_texts: list[str],
    ) -> str | list[str] | tuple[str, ...]:
        return apply_expected_format(result, fallback_texts, self.config.expected_format)

    def _denoise(
        self, texts: list[str], frame: Frame | None
    ) -> str | list[str] | tuple[str, ...]:
        payload = dedupe_lines(texts)
        result, warnings = denoise_once(
            self.ctx.llm,
            texts=payload,
            frame=frame,
            expected_content=self.config.expected_content,
            expected_format=self.config.expected_format,
        )
        self.warnings.extend(f"区域 {self.region_name} {w}" for w in warnings)
        return result
```

> 告警文案：原实现是 `f"区域 {self.region_name} 去噪调用大模型失败，按 R6 降级为规则清理：{exc}"`。新写法前缀 `区域 {name} ` + 函数内文案，**拼出来的字符串基本一致**。若 `test_llm_failure_degrades_to_rules` 检查的是子串“降级”，两个版本都满足。

## P0.5 不要做的事

- ❌ 不要删除 `TranscribeMode` 上的这三个方法（它们是兼容壳，测试与旧调用方都依赖）。
- ❌ 不要把 `dedupe_lines` 也搬进 `denoise_once`（那样会改变 payload 的语义层次）。
- ❌ 不要给 `denoise_once` 加 `retries` 参数（重试是 P1/P3 的职责）。
- ❌ 不要顺手把 `_maybe_adjust_scroll`（第 177-198 行）也重构掉。

## P0.6 验收

```powershell
python -m pytest -q
python -m compileall -q src
```

**完成判定**

- [ ] 测试数仍为 **120 passed, 1 skipped**，0 failed
- [ ] `git diff --stat` 只涉及 `errors.py` / `deepseek_client.py` / `transcribe.py` / 新增 `denoise_ops.py`
- [ ] `grep -r "denoise_per_round"` 数量未变（本步不碰它）
- [ ] `test_llm_failure_degrades_to_rules` 与 `test_description_workflow_collects_clean_unique_lines` 单独跑也通过

---

# 步骤 P1：`LLMWorker` + `PendingText`

## P1.1 目标

新增一个完全独立的异步执行模块：占位符、不可变任务、线程池、幂等结算。**本步骤不接入任何现有代码**，只新增文件与新测试。

## P1.2 必读代码

| 顺序 | 文件 | 读什么 | 为什么 |
| --- | --- | --- | --- |
| 1 | 刚写的 [modes/denoise_ops.py](VibeCoding_Project/src/recruiting_capture/modes/denoise_ops.py) | 全文 | worker 唯一要调的函数 |
| 2 | [llm/client.py](VibeCoding_Project/src/recruiting_capture/llm/client.py) | `LLMClient` 协议 | 确认 `denoise` 的参数名与返回型 |
| 3 | [errors.py](VibeCoding_Project/src/recruiting_capture/errors.py) | `LLMError` / `NonRetryableLLMError` | 重试判定的依据 |
| 4 | [models/config.py](VibeCoding_Project/src/recruiting_capture/models/config.py) | 第 424-446 行（`RunConfig` 字段风格） | P3 要加的字段，这里先了解命名风格即可 |
| 5 | [tests/test_deepseek_client.py](VibeCoding_Project/tests/test_deepseek_client.py) | 第 13-40 行（`FakeSession` / `_client`） | 测试用假客户端写法参考 |
| 6 | [modes/base.py](VibeCoding_Project/src/recruiting_capture/modes/base.py) | 第 21-41 行（`ModeContext`） | 了解 `ctx.llm` / `ctx.sleep` 的注入方式（本步不接入） |

**不要读**：`short_message.py`、`orchestrator.py`。本步骤**完全不碰**它们。

## P1.3 新建 `src/recruiting_capture/llm/worker.py`

### 设计约束（逐条对应你已经踩过的坑）

| 约束 | 防的是什么 |
| --- | --- |
| `PendingText.fulfill` 幂等（先写者赢） | 主线程超时兜底后，worker 迟到的写入会覆盖已兜底结果 |
| 任务是不可变快照（`frozen=True`） | 延迟执行时读到被下一次 `workflow()` 重置的实例状态 |
| worker 用**自己的**客户端实例 | 与主线程的 `confirm_click` 并发争用同一个 `requests.Session` |
| 线程绝不允许因异常退出 | 线程死亡后 `join()` 永久挂起 |
| `queue.full()` 阻塞 submit | 无界队列导致内存爆炸与尾部等待失控 |
| 线程由**主流程**创建/回收，不是模块级单例 | 避免跨测试、跨运行的状态泄漏 |

### 参考实现

```python
"""大模型异步执行：单队列 + 固定线程池 + 占位符结算。

设计要点见 docs/AMBIGUITIES.md D23 / D24。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..capture.driver import Frame
from ..errors import LLMError, NonRetryableLLMError
from ..modes.denoise_ops import denoise_once, rule_based_result
from .client import LLMClient

logger = logging.getLogger(__name__)

_UNRESOLVED = object()   # resolve() 超时哨兵；用身份比较，不可用 ==


class PendingText:
    """去噪结果的占位符：既是槽位也是句柄。

    - 由主线程 ``LLMWorker.submit`` 创建并立即返回；
    - 由 worker 线程 ``fulfill`` 写入；
    - 由主线程在屏障处 ``resolve`` 读取。
    """

    __slots__ = ("seq", "region", "_event", "_value", "_warnings", "_error", "_declared")

    def __init__(self, seq: int, region: str) -> None:
        self.seq = seq
        self.region = region
        self._event = threading.Event()
        self._value: Any = None
        self._warnings: list[str] = []
        self._error: BaseException | None = None
        self._declared = False

    @property
    def is_resolved(self) -> bool:
        return self._declared

    def fulfill(
        self,
        value: Any,
        warnings: list[str] | None = None,
        error: BaseException | None = None,
    ) -> None:
        """幂等写入：先写者赢。"""
        if self._declared:
            return
        self._declared = True
        self._value = value
        self._warnings = list(warnings or [])
        self._error = error
        self._event.set()

    def resolve(self, timeout: float) -> tuple[Any, list[str]]:
        """等待并返回 ``(value, warnings)``。

        - 正常返回已套用期望格式的结果；
        - 超时返回 ``_UNRESOLVED``（调用方必须兜底）；
        - **永不抛异常**。
        """
        if not self._event.wait(timeout=max(0.0, timeout)):
            return _UNRESOLVED, [
                f"区域 {self.region} 异步去噪等待超时（>{timeout:.0f}s），已按规则清理"
            ]
        return self._value, list(self._warnings)


@dataclass(frozen=True)
class DenoiseTask:
    """一次去噪请求的不可变快照。"""

    texts: list[str]
    frame: Frame | None
    expected_content: str
    expected_format: str
    region: str
    attempts: int
    handle: PendingText = field(compare=False)


class LLMWorker:
    """固定大小的后台线程池，按队列顺序执行去噪任务。"""

    def __init__(
        self,
        client: LLMClient,
        *,
        threads: int = 2,
        queue_max: int = 8,
        task_retries: int = 1,
        retry_wait_seconds: float = 2.0,
        sleep=time.sleep,
    ) -> None:
        self._client = client
        self._core_threads = max(1, int(threads))
        self._queue: queue.Queue[DenoiseTask] = queue.Queue(maxsize=max(1, int(queue_max)))
        self._closing = threading.Event()
        self._sleep = sleep
        self._task_retries = max(0, int(task_retries))
        self._retry_wait = max(0.0, float(retry_wait_seconds))
        self._lock = threading.Lock()
        self._seq = 0
        self._handles: list[PendingText] = []
        self.total_calls = 0
        self._threads: list[threading.Thread] = [
            threading.Thread(target=self._run, daemon=True, name=f"llm-worker-{i}")
            for i in range(self._core_threads)
        ]
        for thread in self._threads:
            thread.start()

    # ------------------------------------------------------------------ #
    # 提交
    # ------------------------------------------------------------------ #
    def submit(
        self,
        *,
        texts: list[str],
        frame: Frame | None,
        expected_content: str,
        expected_format: str,
        region: str,
    ) -> PendingText:
        with self._lock:
            self._seq += 1
            handle = PendingText(self._seq, region)
            self._handles.append(handle)
        task = DenoiseTask(
            texts=list(texts),
            frame=frame,
            expected_content=expected_content,
            expected_format=expected_format,
            region=region,
            attempts=self._task_retries + 1,
            handle=handle,
        )
        while True:
            try:
                self._queue.put(task, timeout=0.5)
                logger.debug("提交去噪任务 seq=%d region=%s", handle.seq, region)
                return handle
            except queue.Full:
                if self._closing.is_set():
                    handle.fulfill(
                        rule_based_result(texts, expected_format),
                        [f"区域 {region} 队列已关闭，未提交大模型；已按规则清理"],
                    )
                    return handle

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        while True:
            try:
                task = self._queue.get(timeout=0.2)
            except queue.Empty:
                if self._closing.is_set():
                    return
                continue
            try:
                self._execute(task)
            except BaseException as exc:  # 线程绝不允许因异常退出
                logger.exception("LLM worker 未预期异常（region=%s）", task.region)
                task.handle.fulfill(
                    rule_based_result(task.texts, task.expected_format),
                    [f"区域 {task.region} worker 内部异常：{exc}"],
                )
            finally:
                self._queue.task_done()

    def _execute(self, task: DenoiseTask) -> None:
        warnings: list[str] = []
        value: Any = None
        for attempt in range(1, task.attempts + 1):
            with self._lock:
                self.total_calls += 1
            value, warnings = denoise_once(
                self._client,
                texts=task.texts,
                frame=task.frame,
                expected_content=task.expected_content,
                expected_format=task.expected_format,
            )
            if not warnings:
                task.handle.fulfill(value, [])
                return
            if attempt < task.attempts:
                logger.warning(
                    "区域 %s 去噪失败（第 %d/%d 次），%.1fs 后重试",
                    task.region, attempt, task.attempts, self._retry_wait,
                )
                self._sleep(self._retry_wait)
        task.handle.fulfill(value, warnings)

    # ------------------------------------------------------------------ #
    # 收尾
    # ------------------------------------------------------------------ #
    def close(self, *, drain: bool = True, timeout: float = 30.0) -> list[PendingText]:
        """停止线程池。

        ``drain=True``：先排空队列（正常路径）；
        ``drain=False``：立即放弃（异常/急停路径，避免拖慢退出）。
        返回仍未完成的句柄（已全部兜底，调用方可记录数量）。
        """
        self._closing.set()
        if drain:
            deadline = time.monotonic() + max(0.0, float(timeout))
            for thread in self._threads:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            pending = [h for h in self._handles if not h.is_resolved]
        for handle in pending:
            handle.fulfill(
                None,
                [f"区域 {handle.region} 异步去噪未完成，已放弃"],
            )
        self._abandoned = pending
        return pending
```

> 关于 `close()` 里给未完成句柄写 `value=None`：这是**最后防线**。正常路径下 `resolve` 会在 `close` 之前完成全部结算，这些 `None` 不会被读到。若有句柄确实未被 resolve 就关闭，说明调用方漏了屏障——此时宁可写 `None`，也不能让它永远挂着。

## P1.4 新建 `tests/test_llm_worker.py`

必须覆盖以下 7 项（W1-W7）：

| 编号 | 用例 | 关键断言 |
| --- | --- | --- |
| W1 | 提交后 resolve | 值、`expected_format` 均已套用 |
| W2 | 队列满背压 | `submit` 阻塞一段时间后仍成功，任务不丢 |
| W3 | 可重试失败后成功 | 假客户端第 1 次抛 `LLMError`、第 2 次成功 → `total_calls == 2`，告警为空 |
| W4 | 不可重试失败 | 抛 `NonRetryableLLMError` → **只调用 1 次**，结果已规则兜底，告警非空 |
| W5 | 超时与幂等 | `resolve(0)` 返回 `_UNRESOLVED`；随后 `fulfill` 正常写入；重复 `fulfill` 不覆盖 |
| W6 | 内部异常不杀线程 | 让 `_execute` 抛 `RuntimeError`，后续任务仍被处理 |
| W7 | `close(drain=True)` | 返回的待完成句柄全部 `is_resolved`，无 `None` 泄漏到结果 |

**测试写法约束**（避免 flaky）：

- 假客户端通过 `threading.Event` 控制阻塞时长，不用 `time.sleep` 猜时序；
- 断言使用 `handle.resolve(timeout=5)` 而非无限等待，防止 CI 挂死；
- 每个测试结束必须 `worker.close(drain=False)`，避免线程泄漏影响后续测试。

## P1.5 不要做的事

- ❌ 不要在本步骤修改 `TranscribeMode` / `ShortMessageMode` / `ModeContext` / `RunConfig`。
- ❌ 不要把 worker 做成模块级单例。
- ❌ 不要实现 `denoise_per_round` 相关的批量提交。
- ❌ 不要在 worker 里调用 `confirm_anchor` / `confirm_click`（C-2）。
- ❌ 不要加 `asyncio`。

## P1.6 验收

```powershell
python -m pytest -q tests/test_llm_worker.py -v
python -m pytest -q
```

**完成判定**

- [ ] `test_llm_worker.py` W1-W7 全通过
- [ ] 全量测试仍为 **120 passed, 1 skipped**（旧测试完全没被触动）
- [ ] `git diff --stat` 只有 2 个新增文件
- [ ] `grep -rn "import threading" src/recruiting_capture/` 只命中 `llm/worker.py`

---

# 步骤 P2：两模式接入 worker 与屏障

## P2.1 目标

把 `TranscribeMode` 的 `_denoise` 改为“有 worker 就提交、没 worker 就同步”，并在 `ShortMessageMode` 内部加屏障。**这是唯一改变执行模型的步骤**。

## P2.2 必读代码

| 顺序 | 文件 | 读什么（必须读完整函数） | 为什么 |
| --- | --- | --- | --- |
| 1 | [modes/short_message.py](VibeCoding_Project/src/recruiting_capture/modes/short_message.py) | **全文 364 行** | 屏障要插在 `workflow` 里，`_normalize_key` 要后移 |
| 2 | [modes/transcribe.py](VibeCoding_Project/src/recruiting_capture/modes/transcribe.py) | `workflow` / `_run_single` / `_run_scrollable` | 加 `worker` 透传 |
| 3 | [modes/base.py](VibeCoding_Project/src/recruiting_capture/modes/base.py) | 第 21-41 行 `ModeContext` | P2 只读不改；确认能拿到 `ctx.run` / `ctx.stats` |
| 4 | [llm/worker.py](VibeCoding_Project/src/recruiting_capture/llm/worker.py) | 全文（P1 产出） | 接口：`submit` / `PendingText.resolve` / `close` |
| 5 | [tests/test_modes.py](VibeCoding_Project/tests/test_modes.py) | **全文 188 行** | 第 113-148 行是 SMS 两条路径，第 177 行是 max_items 停止 |
| 6 | [text_utils.py](VibeCoding_Project/src/recruiting_capture/text_utils.py) | `pick_title_candidate` / `clean_title` / `merge_unique` | `_normalize_key` / `_flatten_values` 的依赖 |

**不要读**：`app/streamlit_app.py`。前端与本步无关。

## P2.3 关键改动（逐点）

### (a) 三个必须早发现的事实

1. **`_invoke_transcribe_modes` 里立刻用结果做键**（short_message.py 第 251-263 行）。异步后 `values` 里是占位符，`_normalize_key` 的 `isinstance(value, str)` 判断必然落空 → 键全部变成 `"缺省"`。**必须把做键延后到解析之后**。

2. **`_assemble` 是唯一同时需要所有区域最终值的函数**，且按 `self.items` 顺序遍历。屏障放这里之前，顺序即确定性。

3. **`to_records` 按类型分派**（`isinstance(desc_value, str)` / `(list, tuple)`）。若占位符逃逸到它，`job_desc` 会变成 `[]`。所以屏障**必须**在 `workflow` 返回之前完成。

### (b) `TranscribeMode` 改动

```python
def workflow(self, worker: "LLMWorker | None" = None) -> str | list[str] | tuple[str, ...]:
    ...  # 既有的状态重置保持不变
    if not self.config.scrollable:
        return self._run_single(worker)
    return self._run_scrollable(worker)
```

`_run_single(worker=None)` / `_run_scrollable(worker=None)` 各加一个透传参数，末尾调用改为 `self._denoise(payload, frame, worker)`。

`_denoise` 增加 worker 分支：

```python
def _denoise(self, texts, frame, worker=None):
    payload = dedupe_lines(texts)
    if worker is None:                      # 同步路径：测试与单区域直接调用
        result, warnings = denoise_once(...)
        self.warnings.extend(...)
        return result
    send_frame = self.config.expected_format == FORMAT_STR   # C-6：只有发图片的区域送帧
    return worker.submit(
        texts=payload,
        frame=frame if send_frame else None,
        expected_content=self.config.expected_content,
        expected_format=self.config.expected_format,
        region=self.region_name,
    )
```

> **C-6 的落地方式**：`send_frame` 由 `expected_format` 决定（`str` 区域送帧，`list`/`tuple` 区域只送文本）。这是**二值规则**，不是策略开关，符合 C-8。若后续需要更细的控制，再讨论。
>
> **需要向用户确认**：这与“抄录模式只发初步分析文本、短信模式只发图片”的表述存在细微差异——当前架构里“发图片”这件事是**按区域**而非按模式配置的。用 `expected_format` 判定是能落地的最接近语义。**若用户要求按区域角色（区域 B/其他区域）而不是按格式判定，请在 P2 开始前先询问**。

### (c) `ShortMessageMode` 改动

```python
def workflow(self, max_items: int | None = None) -> dict[str, Any]:
    ...  # 既有重置
    worker = self._make_worker()
    ok = False
    try:
        self._collect(limit, worker)      # 原 while 循环体，整体搬入
        self._resolve_all_pending()       # ← 唯一屏障
        self._assign_titles()             # ← 从 _invoke_transcribe_modes 迁来
        result = self._assemble()
        ok = True
        return result
    finally:
        if worker is not None:
            abandoned = worker.close(drain=ok)
            self.ctx.stats["llm_calls_async"] = (
                self.ctx.stats.get("llm_calls_async", 0) + worker.total_calls
            )
            if abandoned:
                self.warnings.append(
                    f"{len(abandoned)} 个异步去噪未完成，已按规则兜底"
                )
```

三个新方法：

```python
def _make_worker(self):
    if not getattr(self.ctx.run, "llm_async", True):
        return None
    factory = getattr(self.ctx, "llm_factory", None)
    if factory is None:
        return None                        # 降级为同步，不报错
    return LLMWorker(
        factory(),                         # 独立客户端，独立 Session
        threads=int(self.ctx.run.llm_worker_threads),
        queue_max=int(self.ctx.run.llm_queue_max),
        task_retries=int(self.ctx.run.llm_task_retries),
    )

def _resolve_all_pending(self) -> None:
    budget = float(self.ctx.run.llm_task_timeout_seconds)
    started = time.monotonic()
    for item in self.items:                        # 顺序 = 确定性
        for name in self.config.invoke_regions:    # 顺序 = invoke_regions
            handle = item.values.get(name)
            if not isinstance(handle, PendingText):
                continue
            value, warnings = handle.resolve(budget)
            if value is _UNRESOLVED:
                value = rule_based_result(handle_payload_of(handle), ...)
            item.values[name] = value
            item.ordered_results = [
                item.values[n] for n in self.config.invoke_regions
            ]
            for warning in warnings:
                message = f"区域 {name}: {warning}"
                item.warnings.append(message)
                self.warnings.append(message)
    self.ctx.stats["llm_wait_ms"] = int((time.monotonic() - started) * 1000)

def _assign_titles(self) -> None:
    if self.config.data_structure != DATA_STRUCTURE_DICT or not self.key_region:
        return
    for item in self.items:
        title, extra = self._normalize_key(item.values.get(self.key_region))
        item.title = title
        item.warnings.extend(extra)
```

> **`_UNRESOLVED` 的兜底怎么拿到原始 texts**：`PendingText` 不保存 payload。两种做法，二选一，**不要两种都做**：
> - **A（推荐）**：`DenoiseTask` 里保留 `texts`，`close()` 兜底时用 `handle.resolve` 返回的超时告警 + `_UNRESOLVED`，由调用方用**空列表**兜底（`rule_based_result([], fmt)` → `""` 或 `[]`）。此时会丢掉本轮内容——但超时本身意味着 LLM 完全不可用，规则兜底也拿不到数据，告警已说明原因。
> - **B**：`PendingText` 增加一个只读的 `payload: list[str]` 属性（在构造时传入），超时时能真正做规则兜底。
>
> **选 B**，与“R6 降级为规则清理”更一致，且代价只是一个字段。

### (d) 从 `_invoke_transcribe_modes` 删除做键逻辑

原第 259-263 行：

```python
title: str | None = None
if self.config.data_structure == DATA_STRUCTURE_DICT and self.key_region:
    title, key_warnings = self._normalize_key(values.get(self.key_region))
    warnings.extend(key_warnings)
return SmsItem(values=values, ordered_results=ordered, title=title, warnings=warnings)
```

改为：

```python
return SmsItem(values=values, ordered_results=ordered, title=None, warnings=warnings)
```

**`_normalize_key` 函数本身不删**，只是调用点后移到 `_assign_titles`。

## P2.4 新增测试 `tests/test_modes_async.py`

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| A1 | 占位符不外泄 | 递归遍历 `workflow()` 返回值，不含 `PendingText` |
| A3 | 告警归属 | 让第 1 个岗位的 B 区域去噪失败，第 2、3 个正常 → 只有第 1 条记录含该告警 |
| A4 | 键正确性 | 字典模式下 `raw["A"]` 的键是区域 C 的标题，**不是**“缺省” |
| A5 | 同步降级 | `ctx.llm_factory=None` 时 `workflow()` 结果与带 worker 版本一致 |

> **A2（对拍）留到 P3**，因为 `llm_async` 配置项在 P3 才加。在 P2 里可以用 `llm_factory=None/非 None` 做手工对拍，写进执行日志。

## P2.5 不要做的事

- ❌ 不要在 `transcribe.py` 里 import `LLMWorker` 做类型检查以外的事（用 `TYPE_CHECKING` 字符串注解即可）。
- ❌ 不要把 `_assemble` 或 `to_records` 改成异步。
- ❌ 不要让 `_resolve_all_pending` 抛异常（它只收集告警）。
- ❌ 不要改 `SmsItem` 的字段（`ordered_results` 可以在解析后重新赋值，但字段本身不动）。
- ❌ 不要动 `verify_click` 路径（C-2）。

## P2.6 验收

```powershell
python -m pytest -q tests/test_modes.py tests/test_modes_async.py -v
python -m pytest -q
```

**完成判定**

- [ ] `test_modes.py` 全部通过，**一个断言都没改**
- [ ] A1/A3/A4/A5 通过
- [ ] 全量测试通过（P2 后 `test_orchestrator_demo` 可能失败——因为 `llm_calls` 归零，这属**预期**，由 P3 修复；若失败原因不是 `llm_calls`，则本步失败）
- [ ] 手工对拍：`llm_factory=None` 与带 worker 两次运行的 `raw/sms_A.json` 内容一致

---

# 步骤 P3：配置项、工厂注入、报告聚合

## P3.1 目标

让异步成为**可配置、可关闭、可观测**的能力，并修复 `llm_calls`。

## P3.2 必读代码

| 顺序 | 文件 | 读什么 | 为什么 |
| --- | --- | --- | --- |
| 1 | [orchestrator.py](VibeCoding_Project/src/recruiting_capture/orchestrator.py) | 第 107 行（`build_engines` 调用）、第 145-155 行（`ModeContext` 构造）、第 278-316 行（报告） | 注入点与聚合点 |
| 2 | [engines.py](VibeCoding_Project/src/recruiting_capture/engines.py) | 第 24-68 行 | 拆出 `build_llm` |
| 3 | [models/config.py](VibeCoding_Project/src/recruiting_capture/models/config.py) | 第 423-568 行（`RunConfig` 的 FIELDS / from_dict / validate / to_dict 四处） | 新字段要四处同步 |
| 4 | [models/plan_io.py](VibeCoding_Project/src/recruiting_capture/models/plan_io.py) | 第 19-27 行（`_TOP_LEVEL_FIELDS`） | 顶层字段不变，仅供确认 |
| 5 | [tests/test_orchestrator_demo.py](VibeCoding_Project/tests/test_orchestrator_demo.py) | 第 62 行、第 77-89 行 | `llm_calls > 0` 与缺 Key 场景 |
| 6 | [tests/test_config.py](VibeCoding_Project/tests/test_config.py) | 全文 | 回环测试如何校验 FIELDS |

## P3.3 改动清单

### (a) `RunConfig` 新增 6 字段

**必须同时改四处**：字段声明、`FIELDS` 元组、`from_dict`、`to_dict`。漏一处 → `_unknown_keys` 在回环时抛 `ConfigError`。

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `llm_async` | bool | `True` | 总开关；`False` = 完全同步（回滚路径） |
| `llm_worker_threads` | int | `2` | C-1 |
| `llm_queue_max` | int | `8` | 背压上界 |
| `llm_task_retries` | int | `1` | C-4 外层重试次数 |
| `llm_task_timeout_seconds` | int | `90` | 主线程单句柄等待预算 |
| `llm_drain_timeout_seconds` | int | `30` | `close(drain=True)` 的预算 |

`validate()` 里加两条**不抛异常**的告警（避免打断既有计划）：

```python
worst = self.llm_timeout_seconds * (self.llm_max_retries + 1)
if self.llm_async and self.llm_task_timeout_seconds > worst:
    # 仅记录，交给调用方决定是否警告
    self.__dict__["_timeout_warning"] = (
        f"llm_task_timeout_seconds({self.llm_task_timeout_seconds}) 大于单任务最坏耗时"
        f"({worst}s)：主线程可能先放弃等待而 worker 稍后完成（结果会被幂等忽略）"
    )
```

> 不要在 `validate` 里 `raise`。现有测试会在不改 run 配置的情况下调用 `plan.validate()`。

### (b) `engines.py`：拆出 `build_llm`

```python
def build_llm(plan: CapturePlan) -> LLMClient:
    """按计划构建大模型客户端（主线程与 worker 共用同一份配置）。"""
    if plan.run.llm == "deepseek":
        from .llm.deepseek_client import DeepSeekClient
        return DeepSeekClient(
            base_url=plan.run.llm_base_url,
            model=plan.run.llm_model,
            timeout=plan.run.llm_timeout_seconds,
            max_retries=plan.run.llm_max_retries,
        )
    return MockLLMClient()
```

`build_engines` 第 54-65 行替换为 `llm = build_llm(plan)`。

> **C-4 的落地**：`llm_max_retries` 默认从 `2` 改为 `1`（第 434 行）。同时要改 `configs/demo_plan.json` 与 `configs/boss_1080p_example.json` 里的 `"llm_max_retries": 2`。**注意**：`demo_plan.json` 是测试夹具来源（`conftest.py` 的 `demo_plan` fixture），改动后必须重跑对拍。

### (c) `orchestrator.py`：注入工厂

```python
context = ModeContext(
    ...,
    llm_factory=lambda: build_llm(plan),    # ← 必须用 lambda
)
```

**必须是 lambda**：`DeepSeekClient.__init__` 在缺 Key 时抛 `LLMError`。若在这里直接 `build_llm(plan)`，会在 `test_missing_deepseek_key_writes_error_report` 期望的 `build_engines` 位置**之前**多抛一次。用 lambda 后，工厂在 worker 启动时（主线程、`Thread.start()` 之前）才调用，异常仍在主线程抛出。

### (d) `orchestrator.py`：报告聚合

第 312 行改为：

```python
_sync_calls = int(getattr(llm, "calls", 0)) if "llm" in locals() else 0
_async_calls = int((context.stats if context else {}).get("llm_calls_async", 0))
...
"llm_calls": _sync_calls + _async_calls,     # 保持既有语义 = 总数
"llm_calls_sync": _sync_calls,
"llm_calls_async": _async_calls,
"llm_wait_ms": int((context.stats if context else {}).get("llm_wait_ms", 0)),
"llm_async": bool(plan.run.llm_async),
```

> 注意 `_sync_calls` 必须在 `finally` 块里计算——此时所有 `workflow()` 已返回、`close()` 已执行，`stats["llm_calls_async"]` 已落定。

### (e) 新增 A2 对拍测试

在 `tests/test_orchestrator_demo.py` 追加：

```python
def test_async_matches_sync_byte_for_byte(tmp_path, demo_plan, project_root, no_sleep):
    runs = {}
    for flag in (False, True):
        plan = _clone(demo_plan, tmp_path / f"output_{flag}")
        plan.run.llm_async = flag
        run_dir = tmp_path / f"run_{flag}"
        run_plan(plan, run_dir, project_root, sleep=no_sleep)
        runs[flag] = (run_dir / "jobs.jsonl").read_text(encoding="utf-8")
    assert runs[False] == runs[True]
```

> 若因 `captured_at` 时间戳导致不一致，**不要**放宽断言，而是比较去掉 `captured_at` 字段后的规范化 JSON 序列；并在执行日志里说明原因。

## P3.4 不要做的事

- ❌ 不要给 `TranscribeConfig` 加任何字段（C-5 要求删除而不是新增）。
- ❌ 不要动 `_TOP_LEVEL_FIELDS`。
- ❌ 不要在 `RunConfig.validate` 里 `raise` 超时关系。
- ❌ 不要改 `build_engines` 的返回签名。

## P3.5 验收

```powershell
python -m pytest -q
python run.py validate --plan configs/boss_1080p_example.json
python run.py demo
```

**完成判定**

- [ ] 全量测试通过，含新增 A2
- [ ] `capture_report.json` 有 `llm_calls` / `llm_calls_sync` / `llm_calls_async` / `llm_wait_ms` / `llm_async` 五个字段
- [ ] `python run.py demo` 输出 3 个岗位、`list_end`、`llm_calls > 0`
- [ ] `plan_to_dict` ↔ `plan_from_dict` 回环测试通过

---

# 步骤 P4：移除 `denoise_per_round`

## P4.1 目标

彻底移除这个**从未被读取过**的死配置，并删除其界面控件。

## P4.2 为什么必须原子完成

`plan_builder.update_config` 用 `set(TranscribeConfig.FIELDS)` 做白名单（[plan_builder.py](VibeCoding_Project/src/recruiting_capture/plan_builder.py) 第 145-148 行），而界面第 1428 行**显式传入** `"denoise_per_round"`。

- 只删字段、不删界面传参 → 立刻 `ConfigError`
- 只删界面、不删字段 → 无害但残留
- 删字段 + 不清存档计划 → `runtime/plans/plan_2026*.json` 里的旧键触发 `_unknown_keys` 拒绝

所以**四处必须在同一步完成**。

## P4.3 必读代码

| 顺序 | 文件 | 读什么 | 为什么 |
| --- | --- | --- | --- |
| 1 | [models/config.py](VibeCoding_Project/src/recruiting_capture/models/config.py) | 第 113-220 行（`TranscribeConfig` 全类） | 四处（字段/FIELDS/from_dict/to_dict） |
| 2 | [plan_builder.py](VibeCoding_Project/src/recruiting_capture/plan_builder.py) | 第 27-28 行、第 141-151 行、第 220-260 行（摘要渲染） | 白名单与 `_rebuild` 类路径 |
| 3 | [app/streamlit_app.py](VibeCoding_Project/app/streamlit_app.py) | 第 1397-1431 行 | 控件与 `update_config` 传参 |
| 4 | [configs/demo_plan.json](VibeCoding_Project/configs/demo_plan.json) | 第 31-45 行 | 区域 B 的配置 |
| 5 | [configs/boss_1080p_example.json](VibeCoding_Project/configs/boss_1080p_example.json) | 第 31-45 行 | 同上 |
| 6 | [tests/test_streamlit_app_smoke.py](VibeCoding_Project/tests/test_streamlit_app_smoke.py) | 全文 | 前端冒烟是否引用该控件 |
| 7 | [tests/test_plan_builder.py](VibeCoding_Project/tests/test_plan_builder.py) | 第 43 行 `test_update_config_rejects_unknown_key` | 确认不会误伤 |

## P4.4 改动清单

1. **`models/config.py`**：删除 `denoise_per_round` 字段声明（第 128 行）、`FIELDS` 条目（第 143 行）、`from_dict` 解析（第 181-183 行）、`to_dict` 输出（第 217 行）。
2. **`plan_builder.py`**：确认第 220-260 行摘要渲染未引用它（若引用，一并删）。
3. **`app/streamlit_app.py`**：删除第 1403-1407 行控件，及第 1428 行 `"denoise_per_round": denoise_per_round,` 这一行。
4. **`configs/demo_plan.json` / `configs/boss_1080p_example.json`**：删除 `"denoise_per_round": false,` 行。
5. **`docs/AMBIGUITIES.md`**：新增一行，记录“该配置自引入起从未被任何代码读取，已移除；`runtime/plans/*.json` 中的残留键在加载时丢弃”。

### 关于旧存档计划的兼容

`runtime/plans/plan_2026*.json` 与 `runtime/runs/*/capture_report.json` 里**仍会**包含该键。两种处理：

- **推荐（默认）**：加载时丢弃。在 `TranscribeConfig.from_dict` 里，把 `_unknown_keys` 的调用改为先剔除已知废弃键：

```python
_DEPRECATED_KEYS = ("denoise_per_round",)

@classmethod
def from_dict(cls, data):
    data = dict(data)                         # 不改调用方对象
    for key in _DEPRECATED_KEYS:
        data.pop(key, None)
    ...  # 其余不变
```

- 备选：不兼容，要求用户重新保存计划。

**采用推荐方案**，因为该配置**从未生效过**，丢弃是安全的，且不会让历史计划失效。这是 `_DEPRECATED_KEYS` 这种机制的唯一用途——**不要**为它建通用框架（R-07）。

## P4.5 不要做的事

- ❌ 不要“顺便”删除其他可疑字段（`auto_adjust_scroll`、`max_stale_rounds` 都在用）。
- ❌ 不要新建“废弃配置清理模块”。
- ❌ 不要修改 `runtime/` 下的历史产物（那是运行记录，属于证据）。

## P4.6 验收

```powershell
python -m pytest -q
python -m compileall -q src app scripts tests run.py
python run.py validate --plan configs/boss_1080p_example.json
python run.py validate --plan runtime/plans/plan_20261023-025625.json
```

**完成判定**

- [ ] 全量测试通过
- [ ] `grep -rn "denoise_per_round" src app configs` **仅命中** `config.py` 的 `_DEPRECATED_KEYS` 与 `docs/`
- [ ] 旧存档计划 `plan_20261023-025625.json` 校验通过
- [ ] 前端冒烟测试通过

---

# 5. 跨步骤的不变量检查清单

每步收工前逐条确认：

| # | 检查 | 命令 / 方法 |
| --- | --- | --- |
| 1 | 没有非主线程调用 `driver.*` | 审查 `llm/worker.py` 与 `denoise_ops.py`，确认零处引用 `ScreenDriver` |
| 2 | `PendingText` 未出现在任何返回值 | A1 测试 + `grep -n "PendingText" src/` 仅命中 `llm/worker.py` 与 `short_message.py` |
| 3 | `confirm_anchor` / `confirm_click` 仍在主线程 | `grep -n "confirm_" src/recruiting_capture/modes/` 的调用点不在 worker 分支内 |
| 4 | 所有新增 `except` 都有 log 或 warning | R-08 人工审查 |
| 5 | 文档已同步 | `docs/AMBIGUITIES.md` 与 `README.md` 的 diff |
| 6 | 没有引入新依赖 | `git diff requirements.txt` 为空 |

---

# 6. 风险与回滚

| 风险 | 触发条件 | 处理 |
| --- | --- | --- |
| `llm_async=false` 回滚失效 | P3 的 A2 对拍失败 | 只能在 P2 之后回滚——P2 是断点。P0/P1 可单独回退 |
| 线程泄漏 | 每步测试后进程不退出 | `pytest` 结束后应无残留线程；`LLMWorker` 全部 `daemon=True` 且由 `finally` 关闭 |
| 尾部等待过长 | `llm_wait_ms` 持续 > 5000 | 调小 `llm_queue_max`，或把 `llm_worker_threads` 退回 1 观察 |
| 告警归属错乱 | A3 失败 | 说明任务里混入了可变引用，回到 P2.3(d) 检查 |
| 旧计划加载失败 | P4 后 `_unknown_keys` 报错 | 检查 `_DEPRECATED_KEYS` 是否生效 |

**回滚顺序**：P4 → P3 → P2 → P1 → P0（逆序）。每一步都是独立提交，`git revert` 单步即可。

---

# 7. 交付物清单

| 文件 | 状态 | 步骤 |
| --- | --- | --- |
| `src/recruiting_capture/errors.py` | 修改 | P0 |
| `src/recruiting_capture/llm/deepseek_client.py` | 修改 | P0 |
| `src/recruiting_capture/modes/denoise_ops.py` | 新增 | P0 |
| `src/recruiting_capture/modes/transcribe.py` | 修改 | P0、P2 |
| `src/recruiting_capture/llm/worker.py` | 新增 | P1 |
| `tests/test_llm_worker.py` | 新增 | P1 |
| `src/recruiting_capture/modes/short_message.py` | 修改 | P2 |
| `tests/test_modes_async.py` | 新增 | P2 |
| `src/recruiting_capture/modes/base.py` | 修改 | P3 |
| `src/recruiting_capture/engines.py` | 修改 | P3 |
| `src/recruiting_capture/orchestrator.py` | 修改 | P3 |
| `src/recruiting_capture/models/config.py` | 修改 | P3、P4 |
| `configs/demo_plan.json` | 修改 | P3、P4 |
| `configs/boss_1080p_example.json` | 修改 | P3、P4 |
| `app/streamlit_app.py` | 修改 | P4 |
| `src/recruiting_capture/plan_builder.py` | 可能修改 | P4 |
| `tests/test_orchestrator_demo.py` | 追加用例 | P3 |
| `docs/AMBIGUITIES.md` | 修改 | P3、P4 |
| `README.md` | 修改 | P4 |

---

# 8. 执行日志（执行者填写）

每步完成后填写，作为交付证据。

| 步骤 | 完成时间 | 测试结果 | 与预期偏差 | 备注 |
| --- | --- | --- | --- | --- |
| 基线 | | | | |
| P0 | | | | |
| P1 | | | | |
| P2 | | | | |
| P3 | | | | |
| P4 | | | | |

---

# 9. 待讨论 / 未决事项

| 编号 | 事项 | 当前状态                                            |
| --- | --- |-------------------------------------------------|
| O-1 | `send_frame` 按 `expected_format == "str"` 判定（P2.3(b)） | **用户意见**：抄录模式下不区分期望格式是str还是list，一律只发送初步分析文本给大模型 |
| O-2 | `on_item` 逐项落盘（方案 B） | **用户意见**：本次不做                                   |
| O-3 | D13 表述与实际落盘粒度不一致（`docs/AMBIGUITIES.md` 第 21 行写“每条岗位立即落盘”，实际是 sms 级） | **用户意见**：本次不动代码                                 |
| O-4 | `llm_worker_threads` 在真实 DeepSeek 上的实测收益 | **用户意见**： P3 之后用一次真实采集验证（需 API Key），AI无需考虑      |
| O-5 | `llm_wait_ms` 是否需要进前端展示 |  **用户意见**：本次不做，先记录字段                                      |
