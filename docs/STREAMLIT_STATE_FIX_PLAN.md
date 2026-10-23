# 区域配置页 Widget 状态冲突 —— 修正后方案

> 取代此前「`_seed` + 删除 `default`」的方案。上一版基于一个错误前提，落地后测试失败并已回退。
> 本文件只描述**经过 Streamlit 源码核实**的根因与修复路径，供评审后再实施。

---

## 一、失败原因复盘

上一轮没跑通，有三个独立问题叠加，不能简单归为"测试写坏了"：

| # | 问题 | 后果 |
| --- | --- | --- |
| 1 | **把核心 bug 当成了单点修复**。只在 `invoke_` 一处去掉 `default`，其余控件仍用 `value=` + `key=` | 症状消失一半，语义仍然自相矛盾 |
| 2 | **没核实 Streamlit 真实语义就笃定"删掉 default 是错的"** | `_seed` 方案基于的假设，经源码核对只有一半成立（见第二节） |
| 3 | **回归测试试图驱动真实 app 走完整个截图→框选→配置流程** | 假组件语义（`click_and_drag`、`picker_nonce`、`last_pick_signature`）与真实组件不断错位，测试在真正断言之前就耗尽重跑次数；调试过程中改了 3 个文件、写了诊断脚本，最终 `DeepSeek Messages transport failed` 中断 |

**结论**：测试策略必须改为**断言某个替身的语义**，而不是驱动真实 app 走完整流程。

---

## 二、根因（已核对 Streamlit 1.66.1 源码）

### 2.1 项目里同时用着两套互斥的约定

```
A 段 / ①页控件：  _number_value(...), _select_value(...)      ← 防御式：控件不带 key
配置页控件：      value=cfg.get(...) + key="xxx"              ← 旧式：带 key 又传值
```

`_number_value` / `_select_value`（app 第 187–204 行）的存在，本身就是项目早已意识到"控件值可能缺失/过时"的证据。配置页是新加的一批控件，把这套约定丢掉了。

### 2.2 `is_new_state_value` 的真实含义

`streamlit/runtime/state/session_state.py:762`：

```python
def is_new_state_value(self, user_key: str) -> bool:
    """True if a value with the given key is in the current session state."""
    return user_key in self._new_session_state
```

只要本次 run 里**代码写过** `st.session_state[key] = ...`，这个 key 就落入 `_new_session_state`，`is_new_state_value` 即为 `True` —— 无需用户动过控件。

### 2.3 告警与"值被忽略"是两条独立路径

`policies.py:59–96`：只有 `is_new_state_value(key)` 为真且 `default_value is not None` 才告警。**告警只是症状。**

真正的值解析在 `_get_widget_ui_value`（`session_state.py:1670–1685`）：

```python
if widget_id not in self and (user_key is None or user_key not in self) and not url_binding_resolved:
    # 首次注册：把控件默认值写入 widget state
    self._new_widget_state.set_from_value(widget_id, initial_widget_value)

widget_value = cast("T", self[widget_id])   # ← 读的是 widget state
```

`_remove_stale_widgets`（`session_state.py:1416–1457`）证明：**上一轮没渲染的 keyed 控件，其 widget state 会被清掉**。所以"每轮都渲染的控件一定沿用旧值"这个说法过于笼统——真正的问题是**控件一旦从"条件渲染"切换到"持续渲染"，就会把上一轮的自由值锁进 widget state**：

- 抄录模式的 `td_/ts_/tm_` 只在 `scrollable` 为真时渲染 → 勾选后进入持续渲染 → 旧值被锁死；
- `_reset_ui_with_builder` 先把 `opt_quality` / `opt_max_width` 写进 `session_state`，再渲染 ①页控件 → 这些值同样被锁死。

### 2.4 multiselect 的选项校验 → 影响测试设计

`multiselect.py:872–885`：

```python
current_values, value_needs_reset = validate_and_sync_multiselect_value_with_options(
    widget_state.value, indexable_options, key, format_func
)
```

选项变化时，**返回值本身会被自动过滤**（"离开选项的值一律剔除"）。这意味着上一版方案里那段 `stale = [...] ; st.rerun()` 兜底**根本不需要**，它反而会引入额外的 rerun ——这正是测试里"每轮都在 rerun"的来源之一。

---

## 三、真实缺陷判定

| 控件 | 现状 | 是否为真缺陷 | 说明 |
| --- | --- | --- | --- |
| `invoke_`（1269） | `default=` + `key=` | **无法改变** | 值本就被 `validate_and_sync` 裁剪，`default` 永不起作用 |
| 短信模式 `sd_/ss_/sm_/stopmatch_/ds_/maxitems_/verify_/rollback_/clickretry_/smsrounds_` | `value=` + `key=` | 是 | key 持续存在，切模式后 `value=` 被忽略 |
| 抄录模式 `scrollable_` | `value=` + `key=` | 是 | 同上 |
| 抄录模式 `td_/ts_/tm_/tstopmatch_` | `value=` + `key=` | 是，且为 2.3 的锁定场景 | 条件渲染导致旧值锁死 |
| `content_/format_/anchor_/trounds_/adjust_` | `value=` + `key=` | 是 | 加载示例后不更新 |
| `out_title` / `out_desc`（1442–3） | `index=` + `key=` | 是 | "自动推荐"按钮写 builder 后，界面被旧值回写覆盖 |

**触发路径（可复现）**：
框选 A/B/C → A 切短信模式（`set_mode` 重建默认配置，`st.rerun()`）→ 重渲染时 `cfg` 已变，控件仍持旧值 → 用户什么都没改，却覆盖了新配置。

**用户可见现象**：切模式、加载示例配置、点"自动推荐"之后，界面显示的仍是旧值，且会把旧值写回 `builder.configs`。

---

## 四、修复方案

### 4.1 原则

**一个控件的"值"只能有一个所有者。**

- 有 `key` → 值归 session_state 所有，**绝不**再传 `value=` / `default=` / `index=`；
- 无 `key` → 值归 `value=`/`index=` 所有，代码回写后立即生效（即项目既有的 `_number_value` 约定）。

配置页可选**方案 X**（推荐）或 **方案 Y**，二选一并全文贯彻。

### 4.2 方案 X（推荐）：配置区改用无 key 受控控件

配置是"从 `builder` 读出来、改完写回 `builder`"的纯映射，天然适合受控写法：

```python
# 修复前
scroll_distance = st.number_input("滚动距离（负值向下）",
    value=int(cfg.get("scroll_distance") or -180), step=10, key=f"sd_{name}")

# 修复后（去掉 key；沿用既有 _number_value 约定防 None）
scroll_distance = _number_value(
    st.number_input("滚动距离（负值向下）",
        value=int(cfg.get("scroll_distance") or -180), step=10),
    "", int(cfg.get("scroll_distance") or -180),
)
```

好处：

- `builder` 是唯一真值源 → 切模式 / 加载示例 / 自动推荐**立即生效**，无需 `st.session_state` 重播种；
- 告警根因（`default` + `key`）被结构性消除，不是"绕开"；
- 与 ①页既有约定统一，代码风格一致。

代价：`value=` 作为服务端默认值每轮生效；用户编辑后 Streamlit 同样通过 widget state 回传新值，受控写法在无 key 控件上成立（1.66 支持）。

**唯一例外**：`multiselect` 的选项收缩已由框架处理（见 2.4），改为 `default=` 即可，无需额外逻辑。

### 4.3 方案 Y（保守）：保留 key，显式播种 + 失效清理

若希望保留 key（便于 `st.session_state` 调试），则必须把**所有** `value=`/`index=` 换成"播种"：

```python
def _seed(key: str, value: object) -> None:
    """控件只带 key 时唯一的初值来源：key 不存在才写入。"""
    if key not in st.session_state:
        st.session_state[key] = value
```

并在**每一处外部改写 `builder.configs` 的地方**调用失效函数（`set_mode` 后、加载示例后、`remove_region` 后、自动推荐后）：

```python
def _clear_region_widgets(name: str) -> None:
    for prefix in ("mode_", "sd_", "ss_", "sm_", "stopmatch_", "invoke_", "ds_", "key_",
                   "maxitems_", "verify_", "rollback_", "clickretry_", "smsrounds_",
                   "scrollable_", "td_", "ts_", "tm_", "tstopmatch_", "content_",
                   "format_", "anchor_", "trounds_", "adjust_"):
        st.session_state.pop(f"{prefix}{name}", None)
```

**方案 Y 的固有风险**：`_clear_region_widgets` 的调用点必须与"所有改 cfg 的代码路径"**永远保持同步**，未来任何人新增一处 `builder.update_config` 外部调用就会重新引入 bug。**这是上一版失败的深层原因。**

### 4.4 范围外，但建议同批处理

`_reset_ui_with_builder`（696–714）在渲染前直接写 `opt_quality` / `opt_max_width` / `opt_max_height` 到 session_state，随后 ①页控件以 `key=` 绑定 → 这些值同样会被锁死（2.3）。方案 X 顺手把它们也改为无 key 受控即可一并解决。

### 4.5 不可改动的部分

- `plan_builder.py` 的对外接口（`set_mode` / `update_config` / `set_output_regions`）—— 它们本身没有 bug；
- `plan_builder.py:102–107`、`128–132` 中"移除抄录区域时同步清理其它短信区域 `invoke_regions`"的逻辑 —— 正确，保留；
- 不新增依赖，不改 tests 里既有的 `test_streamlit_app_smoke.py` / `test_streamlit_app_capture_flow.py`（除非方案 X 改变其假组件契约）。

---

## 五、测试策略（重写）

上一版失败的根源：用假组件驱动真实 app 走完整流程。**改为分层验证**：

### 5.1 真值源层（纯 pytest，零 Streamlit）

```python
def test_set_mode_replaces_config_and_drops_invoke_refs():
    b = PlanBuilder(1920, 1080)
    b.add_region([212, 300, 620, 930])   # A
    b.add_region([660, 440, 1460, 930])  # B
    b.set_mode("A", MODE_SMS)
    b.update_config("A", {"invoke_regions": ["B"], "key_region": "B"})
    b.set_mode("B", MODE_SMS)            # B 不再是抄录区域
    assert "B" not in b.config("A")["invoke_regions"]
    assert b.config("A")["key_region"] is None
```

覆盖：`set_mode` 清引用、`remove_region` 清引用、`from_plan(sample)` 后 `invoke_regions == ["C", "B"]`。
**这类测试与 UI 无关，永远不会因组件升级而漂移。**

### 5.2 静态约束层（防回潮，最可靠）

直接解析 `app/streamlit_app.py` 的 AST，断言：**同一个控件调用不得同时出现 `key=` 与 `value=`/`default=`/`index=`**。

```python
def test_widget_call_never_mixes_key_with_value():
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in WIDGETS:
            kws = {k.arg for k in node.keywords}
            assert not ("key" in kws and {"value", "default", "index"} & kws), \
                f"第 {node.lineno} 行：{node.func.attr} 同时传了 key 与 value/default/index"
```

这条测试**不需要任何 Streamlit 替身**，却能精确锁死本次 bug 的所有形态，并且会在未来被人重新引入时立刻失败。应作为主防线。

### 5.3 行为层（可选，仅当方案 X 需要验证受控语义时）

若必须验证运行时行为，用 `streamlit.testing.v1.AppTest`（1.66 自带）做**最小片段**测试，而不是驱动整个 app：把配置区渲染抽成可独立调用的函数后再测。**不在本次范围内，列为后续项。**

---

## 六、实施步骤（含验证关卡）

| 步骤 | 内容 | 验证 |
| --- | --- | --- |
| 0 | 删除失败遗留 `tests/test_streamlit_config_state.py`、`diag_tmp.py` | ✅ 已完成，工作区干净 |
| 1 | 写 5.2 静态约束测试（先失败） | 应报出 1269 等具体行号 |
| 2 | 写 5.1 真值源测试（应直接通过） | 证明 plan_builder 无缺陷 |
| 3 | 按 4.2 改造配置区全部控件 | 静态测试转绿 |
| 4 | 同批处理 4.4 的 `opt_*` | 静态测试保持绿 |
| 5 | 跑全量 `pytest -q` | **148 passed**，无 skip 变化 |
| 6 | 手动验证清单（见下） | 见 6.1 |

### 6.1 手动验证清单

1. 框选 A/B/C → A 切短信模式 → 检查"滚动距离"是否变为短信模式默认值（而非抄录模式的旧值）；
2. 点"加载示例配置" → 检查 `invoke_regions` 显示 **C、B**、`key_region` 显示 **C**、标题区域 **C**、描述区域 **B**；
3. 点"自动推荐" → 两个下拉框立即变化；
4. 取消勾选"该区域可滚动"→ 再勾选 → 滚动参数显示为配置值而非残留值；
5. 终端**无** `was created with a default value but also had its value set via the Session State API` 告警。

---

## 七、如果你希望更快推进

可以只做 **步骤 1 + 2 + 5**（约 30 分钟，零行为变更）：先把静态约束测试和真值源测试建起来并提交。这样：

- 本次 bug 的**回归防线**立刻到位；
- 实际的 UI 改造（步骤 3）可以单独评审、单独回滚，不再出现"改代码 + 改测试 + 调假组件"混在一起导致整体回退的情况。

**建议先确认两个决策点：**

1. **方案 X（无 key 受控，推荐）还是方案 Y（保留 key + 播种）？** —— 决定步骤 3 的写法；
2. **是否接受"先提交测试防线、再改 UI"的分两步走？** —— 决定风险是否可控。

---

## 八、第一步执行结果（2026-10-23）

决策已确认：**方案 X + 分两步走**。本节记录第一步（建立防线）的落地情况。

### 8.1 新增文件

| 文件 | 内容 |
| --- | --- |
| `tests/test_streamlit_widget_policy.py` | AST 静态约束（主防线，不依赖 Streamlit 运行时） |
| `tests/test_streamlit_config_source.py` | `PlanBuilder` 真值源不变量（不依赖 UI） |

### 8.2 与计划的偏差

**实际违规数是 42 处，不是计划的 ~20 处。** 静态扫描发现配置页之外还有三组同样形态的控件：

| 位置 | 控件 | 说明 |
| --- | --- | --- |
| 978–1013 | `opt_max_width` / `opt_max_height` / `opt_quality` | 计划 4.4 已预判 |
| 1158–1170 | `xmin_/ymin_/xmax_/ymax_{name}` | **计划外**：区域坐标编辑框，与 `builder.regions` 同为真值源 |
| 1465–1477 | `scroll_x/y/distance/times/interval/delay` | **计划外**：滚动测试页 |
| 1622 | `result_run` | **计划外**：结果页运行目录选择 |

→ 第二步的改造范围需扩大到这 42 处；`test_streamlit_widget_policy.py` 会自动把它们全部列出。

### 8.3 顺带发现的 builder 缺陷（新增）

`plan_builder.py:116–136` 的 `set_mode` 只在**离开短信模式**时清理引用：

```python
if mode == MODE_SMS:
    self.configs[name] = default_sms_config()   # ← 没有任何引用清理
else:
    ... 清理其它区域指向 name 的 invoke_regions / key_region
```

后果：A 为短信模式且 `invoke_regions=["B","C"]` 时，把 C 切成短信模式 → A 仍然调用 C，而 C 已不是抄录区域 → 运行期必然失败。

- 已用 `@pytest.mark.xfail(strict=True)` 标记为 `test_set_mode_sms_cleans_dangling_references`；
- **strict=True** 意味着一旦修复，该测试会因"意外通过"而报错，强制移除标记，不会悄悄失效；
- 修复动作建议与第二步同批进行（`set_mode` 的 SMS 分支需补上与 transcribe 分支对称的清理逻辑）。

### 8.4 验证结果

```
tests/test_streamlit_widget_policy.py   3 passed, 1 failed（预期的 42 处违规）
tests/test_streamlit_config_source.py   6 passed, 1 xfailed
全量 pytest                              158 passed, 1 skipped, 1 xfailed, 1 failed
```

全量唯一的 failed 即主防线——它现在**正确地**卡在第二步的 UI 改造上，行为符合设计。

### 8.5 下一步

第二步：按方案 X 改造上述 42 处控件，并修复 8.3 的 builder 缺陷；验收标准为全量 `pytest` 全部通过（无 failed），再执行 6.1 手动验证清单。

---

## 九、第二步执行结果（2026-10-23）

### 9.1 改造范围

45 处控件全部按方案 X 处理（比第一步统计的 42 处多 3 处：`xmin/ymin/xmax/ymax` 计为独立控件、
`measure_target` 与滚动测试页的 6 个控件在改造中一并纳入）：

| 区域 | 数量 | 处理方式 |
| --- | --- | --- |
| 首屏设置（窗口方式、倒计时、API 路由值） | 3 | 去 key，改受控 |
| 展示设置 `opt_max_width/height/quality` | 3 | 去 key，改受控 |
| 测距目标 `measure_target` | 1 | 去 key，索引随会话值回填 |
| 区域坐标 `xmin/ymin/xmax/ymax` | 4 | 去 key |
| 配置页短信模式 | 13 | 去 key，`multiselect` 用 `default=` |
| 配置页抄录模式 | 12 | 去 key |
| 输出映射 `out_title/out_desc` | 2 | 去 key |
| 滚动测试页 | 7 | 去 key |
| 结果页 `result_run` | 1 | 去 key |
| `api_page_value`（disabled 只读） | 1 | 去 key |

新增辅助函数 `_clock_value()`，沿用既有 `_number_value` / `_select_value` 约定，
负责“会话值 → 合法默认值”的范围收敛。

### 9.2 关键发现：去掉 key 后暴露“元素 ID 重复”

**这是静态测试抓不到、只有真实渲染才会暴露的问题。**

按 Streamlit 源码（`elements/lib/utils.py::compute_and_register_element_id`），
无 `key` 控件的元素 ID 由 **`(控件类型, label, options, index, help, horizontal …)`** 计算。
配置页在 `for name in region_names` 循环里渲染，三个区域的“工作模式”单选框
（label 都是 `"工作模式"`、options 相同、index 相同）因此得到**完全相同的 ID**，
直接抛出 `StreamlitDuplicateElementId` 异常，页面白屏。

**解决办法**：把所有循环内控件的 label 前缀上区域名（`f"{name} 工作模式"`），
并用 `label_visibility="collapsed"` 隐藏，界面观感与原来一致（既有 `expander`
标题已经标明当前区域），但每个控件获得了不同 ID。

> 教训：`key` 原先顺带承担了“身份唯一”的职责。方案 X 去掉 key 后，
> 必须由 label 接过这个职责——否则静态检查全绿、运行时却崩溃。

### 9.3 防线补强

针对 9.2 的教训，`test_streamlit_widget_policy.py` 新增一条静态约束：

- `test_widget_labels_inside_loops_must_be_dynamic`：位于循环体内、**无 key** 且 label 为固定字面量的控件一律报错；
- 带 key 的控件（如“删除”按钮 `key=f"del_{name}"`）不受此条约束，其唯一性由 `test_widget_keys_are_unique` 负责。

### 9.4 builder 缺陷修复

`plan_builder.py`：

- 抽出 `_drop_transcribe_references(name)`，把“某区域不再是抄录区域”的清理逻辑收敛为单一实现；
- `set_mode()` 在**两种模式分支之后**统一调用它（原来只在 transcribe 分支调用）；
- `remove_region()` 复用同一函数，消除重复代码；
- **函数签名与返回值均未改动**；`set_mode` 的空操作语义（切到当前已有模式）保持不变。

`tests/test_streamlit_config_source.py` 中 `xfail(strict=True)` 标记已移除，
`test_set_mode_sms_cleans_dangling_references` 现在是正常 passing 测试。

### 9.5 验证结果

```
tests/test_streamlit_widget_policy.py    5 passed
tests/test_streamlit_config_source.py    7 passed
全量 pytest                               160 passed, 1 skipped, 0 failed
Streamlit AppTest（空状态）                0 exception / 0 error / 0 duplication warning
Streamlit AppTest（加载示例配置）          0 exception / 0 duplication warning
   multiselect[A 调用抄录模式的区域] = ['C', 'B']   ← 与示例配置一致（原 bug 的症状已消失）
   selectbox[A 做键的抄录区域] = C
   selectbox[标题区域] = C ／ selectbox[描述区域] = B
   radio[A 工作模式] = 短信模式；B/C = 抄录模式
```

AppTest 属临时校验脚本（`_verify_apptest.py` / `_verify_apptest2.py`），验证后已删除，未入库。
如需纳入 CI，建议后续以 `streamlit.testing.v1.AppTest` 正式落为测试用例。


