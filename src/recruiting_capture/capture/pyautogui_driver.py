"""pyautogui 真实驱动（NEWREADME 6.1）。

本模块内置“拟人化保护”（human-like 行为模拟）的行为实现；参数模型与默认值
集中在 :mod:`recruiting_capture.models.humanize`（计划文件的 ``run.humanize``）。

* **贝塞尔曲线移动**：三次贝塞尔曲线 + smoothstep 速度曲线 + 逐步手抖；
* **移动过冲回拉**：越过目标 10~110px 后再回拉（距离 ≥30px、85% 概率）；
* **移动-点击确认停顿**：点击前随机停顿 80~280ms，且与移动处于同一把动作锁内；
* **移动终点误差漂移**：落点带 ≤1.5px 的圆内随机偏移，再做 ±1px 落点微调；
* **滚动分步完成**：滚轮按 110~130 格随机拆分，间隔 30~120ms；
* **滚动过冲回拉**：末尾多滚 1 格再回滚 1 格；
* **无意义鼠标晃动**：动作间隙 15% 概率做 1~3 次 ±(1~4)px 的游走再归位；
* **无操作时无意识晃动**：空闲 8~20s 后每 1~4s 做 ±(1~2)px 微动（后台守护线程）；
* **可阻塞工作流程**：每 12~30 次动作插入 1.5~5s 阻塞停顿，间隔/时长可配置。

关闭方式：``PyAutoGuiDriver(humanize=False)``，或环境变量
``RECRUITING_CAPTURE_HUMANIZE=0``（测试环境建议设置，避免改变调用时序与次数）。
细粒度调节统一走 :class:`~recruiting_capture.models.humanize.HumanizeConfig`
（运行期对象为 :class:`HumanProfile`）。

**配置与生命周期**：

* 参数来自计划文件的 ``run.humanize``
  （:class:`recruiting_capture.models.humanize.HumanizeConfig`），
  由 :func:`recruiting_capture.engines.build_driver` 注入；
* ``run.humanize.enabled=false`` 或环境变量 ``RECRUITING_CAPTURE_HUMANIZE=0``
  可整体关闭；
* 空闲微动线程随 ``idle_wander`` 启动，由 ``run_plan`` 的 ``finally`` 统一
  ``close()``；等待人工“继续/结束”时会先暂停，避免鼠标在用户操作前端时乱动。
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import math
import os
import random
import sys
import threading
import time
from collections.abc import Iterator

import numpy as np

from ..errors import AutomationAbortedError, InputInjectionError
from ..models.humanize import HumanProfile
from .driver import Frame
from .input_check import ensure_input_injection

logger = logging.getLogger(__name__)

#: 拟人化总开关的环境变量（"0"/"false"/"off"/"no"/"disable" 视为关闭）。
HUMANIZE_ENV = "RECRUITING_CAPTURE_HUMANIZE"


class _HumanPointer:
    """拟人化鼠标动作实现层。

    与 :class:`PyAutoGuiDriver` 分开，是为了把所有“手感”集中在一处，并保证
    所有落点都经过屏幕裁剪（避开 (0,0) 急停角）与异常翻译（急停 → 业务异常）。
    """

    def __init__(self, pg: object, profile: HumanProfile, *, pause: float = 0.0) -> None:
        self._pg = pg
        self._p = profile
        self._pause = max(0.0, float(pause))
        self._rng = random.Random()
        self._lock = threading.RLock()
        self._fs = getattr(pg, "FailSafeException", None)
        self._supports_pause_kw = self._detect_pause_kw(pg)
        self._screen: tuple[int, int] | None = None
        self._last_pos: tuple[float, float] = (0.0, 0.0)
        self._block_actions = 0
        self._block_at = self._randint(profile.block_every) if profile.block_every else 0
        self._pause_depth = 0
        self._idle_stop = threading.Event()
        self._closed = threading.Event()
        self._idle_thread: threading.Thread | None = None
        self._idle_deadline = time.monotonic()

    # ------------------------------------------------------------------ 基础设施

    @staticmethod
    def _detect_pause_kw(pg: object) -> bool:
        """判断后端是否支持 ``_pause`` 关键字（真实 pyautogui 支持）。"""

        fn = getattr(pg, "moveTo", None)
        if fn is None:
            return False
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):  # pragma: no cover - C 扩展/内置函数
            return False
        if "_pause" in params:
            return True
        return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())

    def _uniform(self, pair: tuple[float, float]) -> float:
        lo, hi = sorted((float(pair[0]), float(pair[1])))
        return self._rng.uniform(lo, hi)

    def _randint(self, pair: tuple[int, int]) -> int:
        lo, hi = sorted((int(pair[0]), int(pair[1])))
        return self._rng.randint(lo, hi)

    def _invoke(self, name: str, *args: object, **kwargs: object) -> object:
        """调用后端动作，并把 pyautogui 的急停翻译成业务异常。"""

        fn = getattr(self._pg, name)
        if self._supports_pause_kw:
            kwargs.setdefault("_pause", False)
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            fs = self._fs
            if isinstance(fs, type) and isinstance(exc, fs):
                raise AutomationAbortedError(
                    "检测到鼠标被移到屏幕左上角，已触发 pyautogui 急停保护"
                ) from exc
            raise

    @contextlib.contextmanager
    def action(self, *, touch: bool = True) -> Iterator[None]:
        """一个完整动作的临界区：持锁 + 屏蔽后端自带 PAUSE + 记录活动时间。

        持锁的意义是防止后台“空闲微动”插进“移动 → 停顿 → 点击”之间。
        """

        with self._lock:
            inner = self._pause_depth > 0
            saved: object | None = None
            if not inner and not self._supports_pause_kw:
                saved = getattr(self._pg, "PAUSE", None)
                if saved is not None:
                    try:
                        setattr(self._pg, "PAUSE", 0.0)
                    except Exception:  # pragma: no cover - 只读后端
                        saved = None
            self._pause_depth += 1
            if touch:
                self._touch()
            try:
                yield
            finally:
                if touch:
                    self._touch()
                self._pause_depth -= 1
                if self._pause_depth == 0 and saved is not None:
                    try:
                        setattr(self._pg, "PAUSE", saved)
                    except Exception:  # pragma: no cover
                        pass

    def _touch(self) -> None:
        self._idle_deadline = time.monotonic() + self._uniform(self._p.idle_after)

    def _sleep(self, seconds: float) -> None:
        """分片睡眠，``close()`` 后可立即返回。"""

        end = time.monotonic() + max(0.0, float(seconds))
        while True:
            remain = end - time.monotonic()
            if remain <= 0.0:
                return
            if self._closed.wait(min(remain, 0.2)):
                return

    def position(self) -> tuple[float, float]:
        try:
            pos = self._pg.position()  # type: ignore[attr-defined]
            self._last_pos = (float(pos[0]), float(pos[1]))
        except Exception:  # pragma: no cover - 后端差异
            pass
        return self._last_pos

    def _size(self) -> tuple[int, int]:
        if self._screen is None:
            try:
                width, height = self._pg.size()  # type: ignore[attr-defined]
                self._screen = (int(width), int(height))
            except Exception:  # pragma: no cover - 后端差异
                self._screen = (0, 0)
        return self._screen

    def _clamp(self, x: float, y: float) -> tuple[float, float]:
        """裁剪到屏幕内，并让坐标远离 (0,0)（pyautogui 急停角）。"""

        width, height = self._size()
        if width <= 0 or height <= 0:
            return (float(x), float(y))
        max_x = max(1.0, float(width) - 2.0)
        max_y = max(1.0, float(height) - 2.0)
        return (min(max(float(x), 1.0), max_x), min(max(float(y), 1.0), max_y))

    # ------------------------------------------------------------------ 路径计算

    @staticmethod
    def _bezier(
        p0: tuple[float, float],
        c1: tuple[float, float],
        c2: tuple[float, float],
        p3: tuple[float, float],
        t: float,
    ) -> tuple[float, float]:
        inv = 1.0 - t
        a = inv * inv * inv
        b = 3.0 * inv * inv * t
        c = 3.0 * inv * t * t
        d = t * t * t
        return (a * p0[0] + b * c1[0] + c * c2[0] + d * p3[0],
                a * p0[1] + b * c1[1] + c * c2[1] + d * p3[1])

    def _control_points(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        dist: float,
        bend_scale: float,
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        if dist < 1e-6:
            return None
        ux = (end[0] - start[0]) / dist
        uy = (end[1] - start[1]) / dist
        nx, ny = -uy, ux
        sign = self._rng.choice((-1.0, 1.0))
        bend = max(0.0, float(bend_scale))
        # 绝对弯曲幅度设上限：长距离移动不至于被甩成夸张的大弧线
        first = sign * min(self._uniform(self._p.curve_bend) * dist, 40.0) * bend
        if self._rng.random() < 0.35:
            second = -first * self._rng.uniform(0.4, 1.0)  # S 形
        else:
            second = first * self._rng.uniform(0.45, 0.9)  # 单弧
        t1 = self._rng.uniform(0.18, 0.42)
        t2 = self._rng.uniform(0.58, 0.82)
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        c1 = (start[0] + dx * t1 + nx * first, start[1] + dy * t1 + ny * first)
        c2 = (start[0] + dx * t2 + nx * second, start[1] + dy * t2 + ny * second)
        return c1, c2

    def _travel(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        duration: float,
        *,
        bend_scale: float = 1.0,
    ) -> None:
        """沿贝塞尔曲线分步移动，``duration`` 为总耗时（秒）。"""

        duration = max(0.02, float(duration))
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        dist = math.hypot(dx, dy)
        steps = int(max(8.0, dist / 7.0, duration / 0.010))
        steps = min(steps, 160)
        ctrl = self._control_points(start, end, dist, bend_scale)
        per_step = duration / steps
        # Windows 上 time.sleep 有 ~1ms 起跳粒度，逐点睡眠会把总时长撑大；
        # 按 ~10ms 一批睡眠，并按“计划时间轴”对齐，既保留 60fps 级别的轨迹点，
        # 又让总耗时贴近 duration（调度开销在后续批次中被自然吸收）。
        batch = max(1, int(0.010 / max(per_step, 1e-4)))
        pending = 0
        began = time.perf_counter()
        tremor = max(0.0, float(self._p.tremor_px))
        for i in range(1, steps + 1):
            progress = i / steps
            # smoothstep：起步慢、中段快、收尾慢
            eased = progress * progress * (3.0 - 2.0 * progress)
            if ctrl is None:
                px = start[0] + dx * eased
                py = start[1] + dy * eased
            else:
                px, py = self._bezier(start, ctrl[0], ctrl[1], end, eased)
            if i >= steps:
                px, py = end
            else:
                if tremor > 0.0:
                    px += self._rng.gauss(0.0, tremor * 0.5)
                    py += self._rng.gauss(0.0, tremor * 0.5)
                px, py = self._clamp(px, py)
            self._invoke("moveTo", int(round(px)), int(round(py)), duration=0.0)
            self._last_pos = (px, py)
            pending += 1
            if pending >= batch or i >= steps:
                pending = 0
                due = began + duration * progress * self._rng.uniform(0.98, 1.02)
                remaining = due - time.perf_counter()
                if remaining > 0.0:
                    time.sleep(remaining)

    def _settle(self, target: tuple[float, float]) -> None:
        """落点微调：接近目标后再做 1~3 次 ±1px 的修正。"""

        for _ in range(max(0, self._randint(self._p.settle_times))):
            px, py = self._clamp(
                self._last_pos[0] + self._rng.uniform(-1.0, 1.0),
                self._last_pos[1] + self._rng.uniform(-1.0, 1.0),
            )
            self._invoke("moveTo", int(round(px)), int(round(py)), duration=0.0)
            self._last_pos = (px, py)
            time.sleep(self._rng.uniform(0.01, 0.04))

    def _target(self, x: float, y: float, *, drift: bool) -> tuple[float, float]:
        tx, ty = float(x), float(y)
        radius = max(0.0, float(self._p.drift_px))
        if drift and radius > 0.0:
            # 圆内均匀采样，保证偏移不超过半径
            r = radius * math.sqrt(self._rng.random())
            angle = self._rng.uniform(0.0, 2.0 * math.pi)
            tx += r * math.cos(angle)
            ty += r * math.sin(angle)
        return self._clamp(tx, ty)

    # ------------------------------------------------------------------ 动作

    def move(
        self,
        x: float,
        y: float,
        *,
        duration: float | None = None,
        drift: bool = True,
    ) -> tuple[float, float]:
        """拟人化移动：贝塞尔曲线 → （可选）过冲 → 回拉 → 落点微调。"""

        start = self.position()
        target = self._target(x, y, drift=drift)
        dist = math.hypot(target[0] - start[0], target[1] - start[1])
        total = float(duration) if duration else self._uniform(self._p.move_duration)
        total = min(max(total, 0.05), max(0.05, float(self._p.move_max_duration)))

        if dist < 2.0:
            self._invoke("moveTo", int(round(target[0])), int(round(target[1])), duration=0.0)
            self._last_pos = target
            return target

        if (
            self._p.overshoot_chance > 0.0
            and dist >= 30.0
            and self._rng.random() < float(self._p.overshoot_chance)
        ):
            over = self._uniform(self._p.overshoot_px)
            ux = (target[0] - start[0]) / dist
            uy = (target[1] - start[1]) / dist
            overshoot_point = self._clamp(target[0] + ux * over, target[1] + uy * over)
            self._travel(start, overshoot_point, total)
            time.sleep(self._uniform((0.03, 0.10)))
            self._travel(
                overshoot_point,
                target,
                self._uniform((0.08, 0.20)),
                bend_scale=0.5,
            )
        else:
            self._travel(start, target, total)

        self._settle(target)
        return self._last_pos

    def pre_click_pause(self) -> None:
        """移动与点击之间的确认停顿。"""

        time.sleep(max(0.02, self._uniform(self._p.pre_click_pause)))

    def click(self) -> None:
        """在当前位置点击（不重新定位，保证按下的位置就是刚才停稳的位置）。"""

        self._invoke("click")
        self._last_pos = self.position()

    def scroll(self, amount: int) -> None:
        """分步滚动 + 过冲回拉。"""

        amount = int(amount)
        if amount == 0:
            return
        direction = 1 if amount > 0 else -1
        remaining = abs(amount)
        plan: list[int] = []
        while remaining > 0:
            chunk = min(remaining, self._randint(self._p.scroll_step))
            plan.append(chunk)
            remaining -= chunk

        for chunk in plan:
            self._invoke("scroll", direction * chunk)
            time.sleep(max(0.0, self._uniform(self._p.scroll_interval)))

        overshoot = (
            self._p.scroll_overshoot
            and abs(amount) >= 2
            and self._rng.random() < 0.8
        )
        if overshoot:
            time.sleep(self._uniform((0.03, 0.10)))
            self._invoke("scroll", direction)
            time.sleep(self._uniform((0.04, 0.12)))
            self._invoke("scroll", -direction)

    def fidget(self) -> None:
        """无意义晃动：在当前位置附近游走 1~3 次再归位。"""

        with self.action():
            origin = self.position()
            for _ in range(self._rng.randint(1, 3)):
                amp = self._uniform(self._p.fidget_px)
                px, py = self._clamp(
                    origin[0] + self._rng.uniform(-1.0, 1.0) * amp,
                    origin[1] + self._rng.uniform(-1.0, 1.0) * amp,
                )
                self._invoke("moveTo", int(round(px)), int(round(py)), duration=0.0)
                self._last_pos = (px, py)
                time.sleep(self._rng.uniform(0.04, 0.14))
            self._invoke("moveTo", int(round(origin[0])), int(round(origin[1])), duration=0.0)
            self._last_pos = origin

    def gap(self) -> None:
        """动作间隔：可能插入阻塞停顿与无意义晃动。"""

        self._touch()
        self._maybe_block()
        time.sleep(max(self._pause, self._uniform(self._p.action_gap)))
        if self._p.fidget_chance > 0.0 and self._rng.random() < float(self._p.fidget_chance):
            self.fidget()

    def _maybe_block(self) -> None:
        every = self._p.block_every
        if not every or self._block_at <= 0:
            return
        self._block_actions += 1
        if self._block_actions < self._block_at:
            return
        self._block_actions = 0
        self._block_at = max(1, self._randint(every))
        self.blocking_pause()

    def blocking_pause(self, seconds: float | None = None) -> float:
        """阻塞当前工作流一段时间（不移动鼠标），返回实际时长。"""

        duration = (
            float(seconds) if seconds is not None else self._uniform(self._p.block_duration)
        )
        duration = max(0.0, duration)
        logger.debug("拟人化阻塞 %.2fs", duration)
        self._sleep(duration)
        self._touch()
        return duration

    # ------------------------------------------------------------------ 空闲微动

    def start_idle(self) -> None:
        if self._idle_thread is not None and self._idle_thread.is_alive():
            return
        if self._closed.is_set():
            return
        self._idle_stop.clear()
        self._idle_deadline = time.monotonic() + self._uniform(self._p.idle_after)
        thread = threading.Thread(
            target=self._idle_loop,
            name="recruiting-capture-idle-wander",
            daemon=True,
        )
        self._idle_thread = thread
        thread.start()
        logger.debug("空闲微动线程已启动")

    def stop_idle(self) -> None:
        thread = self._idle_thread
        self._idle_stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._idle_thread = None

    def _idle_loop(self) -> None:
        while not self._idle_stop.wait(0.2):
            if self._closed.is_set():
                return
            if time.monotonic() < self._idle_deadline:
                continue
            if not self._lock.acquire(blocking=False):
                self._idle_deadline = time.monotonic() + self._uniform(self._p.idle_interval)
                continue
            self._lock.release()
            try:
                with self.action(touch=False):
                    self._wander()
            except AutomationAbortedError:
                # 后台线程无法中断主流程，只停止自身，主流程会在下次调用时感知急停
                logger.warning("空闲微动触发急停保护，已停止空闲微动")
                self._idle_stop.set()
                return
            except Exception:  # pragma: no cover - 依赖具体桌面环境
                logger.debug("空闲微动异常，已忽略", exc_info=True)
            self._idle_deadline = time.monotonic() + self._uniform(self._p.idle_interval)

    def _wander(self) -> None:
        origin = self.position()
        if origin[0] <= 2.0 and origin[1] <= 2.0:
            return
        amp = float(self._randint(self._p.idle_px))
        for _ in range(self._rng.randint(1, 2)):
            px, py = self._clamp(
                origin[0] + self._rng.uniform(-amp, amp),
                origin[1] + self._rng.uniform(-amp, amp),
            )
            self._invoke("moveTo", int(round(px)), int(round(py)), duration=0.0)
            self._last_pos = (px, py)
            time.sleep(self._rng.uniform(0.04, 0.12))

    def close(self) -> None:
        self._closed.set()
        self.stop_idle()
        logger.debug("拟人化指针已停止")


class PyAutoGuiDriver:
    """基于 pyautogui 的屏幕驱动（内置拟人化保护）。

    pyautogui 在导入时会探测显示环境，因此延迟到实例化时导入。
    构造时执行一次“鼠标输入注入自检”：Win32 的 ``SetCursorPos`` 在缺少
    注入权限时返回失败而不抛异常，若不提前校验，表现为“日志正常、鼠标不动”。
    """

    def __init__(
        self,
        pause: float = 0.1,
        *,
        verify_input: bool = True,
        humanize: bool | None = None,
        human: HumanProfile | None = None,
        idle_wander: bool = False,
    ) -> None:
        import pyautogui

        self._pyautogui = pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = pause
        self._pause = max(0.0, float(pause))

        enabled = self._humanize_by_env() if humanize is None else bool(humanize)
        self._human: _HumanPointer | None = None
        if enabled:
            self._human = _HumanPointer(pyautogui, human or HumanProfile(), pause=self._pause)

        logger.info(
            "pyautogui 驱动已初始化（PAUSE=%.2f, FAILSAFE=True, 拟人化=%s, 解释器=%s）",
            pause,
            "开启" if self._human else "关闭",
            sys.executable,
        )
        if verify_input:
            self.verify_input_injection()
        if idle_wander and self._human is not None:
            self.start_idle_wander()

    @staticmethod
    def _humanize_by_env() -> bool:
        """环境变量总开关；未设置时默认开启。"""

        raw = os.environ.get(HUMANIZE_ENV)
        if raw is None:
            return True
        return raw.strip().lower() not in {"0", "false", "off", "no", "disable", "disabled"}

    @property
    def humanized(self) -> bool:
        """当前是否启用拟人化。"""

        return self._human is not None

    def verify_input_injection(self) -> None:
        """确认本进程能真正移动鼠标；失败抛 :class:`InputInjectionError`。"""

        try:
            ensure_input_injection()
        except InputInjectionError:
            logger.error("鼠标输入注入自检失败（解释器=%s）", sys.executable)
            raise
        logger.info("鼠标输入注入自检通过")

    def size(self) -> tuple[int, int]:
        width, height = self._pyautogui.size()
        return int(width), int(height)

    def capture(self, rect: tuple[int, int, int, int], label: str | None = None) -> Frame:
        try:
            image = self._pyautogui.screenshot(region=tuple(int(v) for v in rect))
        except self._pyautogui.FailSafeException as exc:  # type: ignore[attr-defined]
            raise AutomationAbortedError(
                "检测到鼠标被移到屏幕左上角，已触发 pyautogui 急停保护"
            ) from exc
        except Exception as exc:  # pragma: no cover - 依赖具体桌面环境
            raise AutomationAbortedError(f"屏幕截图失败：{exc}") from exc
        array = np.array(image)
        logger.debug("截图完成 region=%s size=%s label=%s", rect, image.size, label)
        return Frame(image=array, region=tuple(int(v) for v in rect), label=label, meta={})

    def move_to(
        self,
        x: int,
        y: int,
        duration: float = 0.01,
        *,
        precise: bool = False,
    ) -> None:
        """移动鼠标。

        拟人化开启时 ``duration`` 仅作为下限（≥0.15s 才生效），实际节奏由
        :class:`HumanProfile` 决定；``precise=True`` 关闭终点误差漂移。
        """

        human = self._human
        if human is None:
            try:
                self._pyautogui.moveTo(int(x), int(y), duration=max(0.0, float(duration)))
            except self._pyautogui.FailSafeException as exc:  # type: ignore[attr-defined]
                raise AutomationAbortedError(
                    "鼠标移动触发急停保护（鼠标位于屏幕左上角）"
                ) from exc
            return

        floor = float(duration) if duration and float(duration) >= 0.15 else None
        with human.action():
            human.move(int(x), int(y), duration=floor, drift=not precise)
        human.gap()

    def click(self, x: int | None = None, y: int | None = None) -> None:
        """点击；给坐标时按“移动 → 确认停顿 → 按下”的拟人序列执行。"""

        human = self._human
        if human is None:
            try:
                if x is None or y is None:
                    self._pyautogui.click()
                else:
                    self._pyautogui.click(int(x), int(y))
            except self._pyautogui.FailSafeException as exc:  # type: ignore[attr-defined]
                raise AutomationAbortedError(
                    "点击触发急停保护（鼠标位于屏幕左上角）"
                ) from exc
            return

        with human.action():
            if x is not None and y is not None:
                human.move(int(x), int(y))
                human.pre_click_pause()
            else:
                human.pre_click_pause()
            human.click()
        human.gap()

    def scroll(self, amount: int) -> None:
        """分步滚动（含过冲回拉）。

        ``pyautogui.scroll`` 不返回成功与否，因而无法在本层判断滚动是否真的生效；
        注入能力由构造时的 :meth:`verify_input_injection` 统一把关。
        """

        human = self._human
        if human is None:
            try:
                self._pyautogui.scroll(int(amount))
            except self._pyautogui.FailSafeException as exc:  # type: ignore[attr-defined]
                raise AutomationAbortedError(
                    "滚动触发急停保护（鼠标位于屏幕左上角）"
                ) from exc
            return

        with human.action():
            human.scroll(int(amount))
        human.gap()

    # ------------------------------------------------------------ 拟人化扩展接口

    def human_pause(self, seconds: float | None = None) -> float:
        """插入一段可阻塞工作流的拟人停顿（秒），返回实际睡眠时长。

        ``seconds=None`` 时按配置的阻塞区间随机取值。
        """

        human = self._human
        if human is None:
            delay = max(0.0, float(seconds or 0.0))
            time.sleep(delay)
            return delay
        return human.blocking_pause(seconds)

    def fidget(self) -> None:
        """手动触发一次无意义鼠标晃动（供工作流在等待点调用）。"""

        if self._human is not None:
            self._human.fidget()

    def start_idle_wander(self) -> None:
        """启动“无操作时无意识晃动”后台线程（需在退出时 :meth:`close`）。"""

        if self._human is None:
            logger.debug("拟人化已关闭，忽略 start_idle_wander()")
            return
        self._human.start_idle()

    def stop_idle_wander(self) -> None:
        """停止空闲微动线程（可再次启动）。"""

        if self._human is not None:
            self._human.stop_idle()

    def close(self) -> None:
        """释放后台线程；此后所有拟人停顿立即返回。"""

        if self._human is not None:
            self._human.close()

    def __enter__(self) -> "PyAutoGuiDriver":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
