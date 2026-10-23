# 鼠标完全无动作 —— 根因与代码层加固

> 结论先行：**这不是 pyautogui、不是窗口焦点、也不是配置页控件的问题。**
> 真正原因是：`venv\Scripts\python.exe` 所代表的进程链**没有向桌面注入鼠标输入的权限**，
> 而 Win32 的 `SetCursorPos` 在这种情形下**返回 0 表示失败，却不抛任何异常**，
> pyautogui 也不检查返回值。于是日志每步都“成功”，鼠标纹丝不动。

---

## 一、现象

- 滚动测试日志完整：`第 1/5 次滚动：-350` … `第 5/5 次滚动：-350`，无任何报错；
- 真实采集日志也正常：`区域 A 点击坐标 (342, 370)`、`区域 C 滚动 -350`；
- 但鼠标**完全没有移动、滚动或点击**。

---

## 二、排查过程与证据

### 2.1 排除 pyautogui 本身

在终端直接运行，光标正常移动：

```
before Point(x=1113, y=659)
after  Point(x=500, y=400)     ← 正常
```

### 2.2 定位到解释器差异（决定性）

同一份代码、同一个 pyautogui 包，两个解释器行为相反：

| 测试项 | `D:\python\python.exe` | `venv\Scripts\python.exe` |
| --- | --- | --- |
| `pyautogui.moveTo()` | 光标移动 ✓ | 光标不动 ✗ |
| `SetCursorPos()` 返回值 | **1（成功）** | **0（失败）** |
| Windows 会话 / 桌面 | WinSta0 / Default | WinSta0 / Default（相同） |
| 完整性级别 | Medium | Medium（相同） |
| 交替测试 3 轮 | 全部 1 | 全部 0 |

补充对照：

- venv 进程再派生 `D:\python\python.exe` 子进程，**子进程的 `SetCursorPos` 同样返回 0** →
  失败沿进程链继承；
- 同一个 `D:\python\python.exe` 由 PowerShell 直接启动时返回 1。

### 2.3 venv 启动器的特征

```
venv\Scripts\python.exe  OriginalFilename = py.exe     ← Python 启动器包装
D:\python\python.exe     OriginalFilename = python.exe
```

`pyvenv.cfg` 显示 `include-system-site-packages = true`、`base_prefix = D:\python`。
venv 本身是空壳（只有 pip/setuptools），依赖全部来自系统站点包。

### 2.4 为什么日志会“看起来全对”

```python
pyautogui.scroll(args.distance)                          # 返回 None，不判断成败
print(f"第 {index}/{args.times} 次滚动：{args.distance}")  # 无条件打印
```

`SetCursorPos` 返回 0 时 pyautogui 不抛异常，脚本便“成功”跑完 5 次。
这正是本 bug 排查成本极高的原因：**失败被完全静默**。

---

## 三、修复

### 3.1 立即解决（运行方式）

用系统解释器启动 Streamlit，不要用 venv 里的启动器：

```powershell
cd VibeCoding_Project
D:\python\python.exe -m streamlit run app\streamlit_app.py
```

### 3.2 代码层加固（本次已实施）

核心思路：**把“静默失败”变成“显式异常”**，并给出可操作的修复建议。

#### 新增 `src/recruiting_capture/capture/input_check.py`

| 函数 | 职责 |
| --- | --- |
| `probe_cursor_control(user32=None)` | “移动光标 → 读回坐标 → 复原”的往返验证；失败抛 `InputInjectionError`，异常信息含起点/目标/实际坐标、`SetCursorPos` 返回值、解释器路径与修复命令 |
| `ensure_input_injection(...)` | 语义化别名 |
| `describe_input_environment()` | 返回 `解释器=...；会话=...`，便于写日志 |

要点：

- **失败也必然复原光标**（`try/finally`），不干扰用户；
- 只偏移 7px，不会把鼠标甩出屏幕；
- 同时覆盖两种故障：返回 0，以及“返回成功但光标没动”。

#### 新增异常 `InputInjectionError(AutomationAbortedError)`

沿用既有异常层级，CLI 的 `RecruitingError` 统一处理路径自动生效。

#### `capture/pyautogui_driver.py`

- 构造时执行 `verify_input_injection()`，失败即抛错——**在第一次操作之前就失败**；
- 保留 `verify_input: bool = True` 开关，便于测试替身/特殊场景关闭；
- 初始化日志改为 INFO 并打印 `sys.executable`，下次一眼看出跑在哪个解释器下。

#### `cli.py::cmd_scroll_test`

- 启动前先做注入自检，失败直接返回 1 并打印诊断；
- `moveTo` 后**读回光标位置**，未到位则判定失败并中止，不再打印“成功”。

#### `scripts/scroll_test.py`

独立脚本内联一份最小自检（不依赖 `src` 导入路径），`moveTo` 后同样校验落点。

---

## 四、验证

### 4.1 自检在两种解释器下的真实表现

```
=== venv 解释器（故障场景）===
RESULT: BLOCKED
鼠标输入注入自检失败：SetCursorPos 未能移动光标。
  起点=(761, 710) 目标=(768, 710) 实际=(761, 710) SetCursorPos 返回值=0
当前解释器无法向桌面注入鼠标输入。请改用系统解释器启动，例如
    D:\python\python.exe -m streamlit run app\streamlit_app.py
...

=== 系统解释器（正常场景）===
RESULT: OK - cursor injection works
```

### 4.2 真实驱动

```
venv    → DRIVER CHECK: RAISED InputInjectionError（符合预期）
system  → DRIVER CHECK: constructed OK；verify_input_injection: PASS
```

### 4.3 测试

新增 `tests/test_input_injection.py`（7 项），用伪造 user32 覆盖：

1. 光标正常移动 → 通过；
2. `SetCursorPos` 返回 0 → 抛错，且信息含修复建议；
3. 返回值“成功”但光标没动 → 抛错；
4. `GetCursorPos` 读取失败 → 抛错；
5. 探测失败也必须复原光标；
6. `ensure_input_injection` 别名可用；
7. 环境描述含解释器路径。

全量结果：**169 passed, 1 skipped, 0 failed**。

---

## 五、经验教训

1. **“日志正常”不等于“操作生效”**。凡是调用 Win32 而忽略返回值的代码，都是在制造
   静默失败；这次是 `SetCursorPos`/`mouse_event`，下次可能是别的 API。
2. **排查顺序应从“最小可复现”开始**。我最初假设是窗口焦点问题，方向错了；
   直接跑一行 `SetCursorPos` 并打印返回值，五分钟就能定位。
3. **venv 不等于系统 Python**。`--system-site-packages` 的 venv 虽然能 import 到包，
   但它的启动器进程在输入注入权限上可能与系统解释器不同。
4. **用户的第一手观察是最强的线索**。“滚动测试时鼠标完全不动”直接排除了焦点、
   页面内容、OCR 等所有环节——因为滚动测试根本不涉及这些。
