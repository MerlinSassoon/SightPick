"""前端控件的会话状态约束（静态检查）。

背景
----
Streamlit 的控件若同时带 ``key`` 与 ``value`` / ``default`` / ``index``，
只要该 key 对应的值已存在于 ``st.session_state``，代码里写的默认值就会被**静默忽略**
（``streamlit/runtime/state/policies.py::check_session_state_rules`` 只会打一条告警，
真正决定返回值的是 ``session_state.py`` 中的 widget state 解析）。

本项目既有的约定是：**一个控件的“值”只能有一个所有者**。
只要某个控件的 key 被别人（另一个控件、按钮回调、自动推荐等）也在写，
``value=`` / ``default=`` / ``index=`` 就会失效，表现为“界面显示旧值、并把它写回配置”。

约定
----
1. 带 ``key`` 的控件**不得**再传 ``value`` / ``default`` / ``index``；
2. 每个控件的 ``key`` 在模块内**唯一**（重复 key 会共用同一份状态）；
3. 所有带 ``key`` 的控件都必须显式写出 ``key=``，以便上一条能真正生效。

本文件只做 **AST 静态分析**，不依赖 Streamlit 运行时，也不驱动 app 的真实交互流程，
因此不会因为第三方组件（如 ``streamlit_image_coordinates``）的版本差异而失效。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app" / "streamlit_app.py"

#: Streamlit 中“会把值写入 session_state”的输入类控件。
WIDGET_NAMES = frozenset(
    {
        "button",
        "checkbox",
        "color_picker",
        "date_input",
        "file_uploader",
        "multiselect",
        "number_input",
        "radio",
        "select_slider",
        "selectbox",
        "slider",
        "text_area",
        "text_input",
        "time_input",
        "toggle",
    }
)

#: 与 ``key`` 互斥的“默认值”参数名。
VALUE_KWARGS = frozenset({"value", "default", "index"})


class _WidgetCall:
    __slots__ = ("lineno", "name", "keyword_args")

    def __init__(self, lineno: int, name: str, keyword_args: dict[str, ast.expr]) -> None:
        self.lineno = lineno
        self.name = name
        self.keyword_args = keyword_args

    @property
    def span(self) -> set[str]:
        return _literal_names(self.keyword_args.get("key"))

    @property
    def conflicts(self) -> set[str]:
        return set(self.keyword_args) & VALUE_KWARGS

    def __repr__(self) -> str:  # pragma: no cover - 仅用于失败信息
        return f"<{self.name} @ line {self.lineno}>"


def _key_name(node: ast.AST) -> str | None:
    """把 ``st.xxx`` / ``col.xxx`` 归一化为控件名；不是 st.* 调用则返回 None。"""

    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in WIDGET_NAMES:
        return func.attr
    return None


def _literal_names(expr: ast.expr | None) -> set[str]:
    """把 key 表达式解析成可能的名字集合。

    ``key="a"``            -> {"a"}
    ``key=f"sd_{name}"``   -> {"sd_*"}   （用 ``*`` 表示动态段）
    """

    if expr is None:
        return set()
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return {expr.value}
    if isinstance(expr, ast.JoinedStr):
        parts: list[str] = []
        for value in expr.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("*")
        return {"".join(parts)}
    return set()


def _collect_widgets(tree: ast.AST) -> list[_WidgetCall]:
    found: list[_WidgetCall] = []
    for node in ast.walk(tree):
        name = _key_name(node)
        if name is None:
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
        found.append(_WidgetCall(node.lineno, name, kwargs))
    found.sort(key=lambda item: item.lineno)
    return found


@pytest.fixture(scope="module")
def app_source() -> str:
    return APP_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def widgets(app_source: str) -> list[_WidgetCall]:
    return _collect_widgets(ast.parse(app_source, filename=str(APP_PATH)))


def test_analyzer_finds_the_expected_widget_count(widgets: list[_WidgetCall]) -> None:
    """守卫：AST 解析本身不能“空转”（例如所有调用都带位置参数而未被识别）。"""

    assert len(widgets) >= 20, f"只识别到 {len(widgets)} 个控件，请检查解析逻辑"


def test_keyed_widget_must_not_pass_value_default_or_index(
    widgets: list[_WidgetCall],
) -> None:
    """核心约束：``key`` 与 ``value`` / ``default`` / ``index`` 不得同时出现。

    这是本文件存在的主要原因：两者共存时默认值会被静默丢弃，
    造成“控件显示旧值并覆盖新配置”的缺陷。
    """

    offenders = [w for w in widgets if w.span and w.conflicts]
    if offenders:
        lines = "\n".join(
            f"  app/streamlit_app.py:{w.lineno}  st.{w.name}("
            f"key={sorted(w.span)}, {sorted(w.conflicts)})"
            for w in offenders
        )
        pytest.fail(
            "以下控件同时传了 key 与 value/default/index，默认值会被 session state 静默忽略：\n"
            f"{lines}\n"
            "修复方向见 docs/STREAMLIT_STATE_FIX_PLAN.md 方案 X："
            "配置读自 builder 的控件去掉 key=，只用 value=/index= 受控。",
            pytrace=False,
        )


def test_widget_keys_are_unique(widgets: list[_WidgetCall]) -> None:
    """重复的 key 会让两个控件共用同一份状态，后渲染者覆盖前者。"""

    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for widget in widgets:
        for name in widget.span:
            if "*" in name:  # 动态 key（如 sd_{name}）逐个区域天然唯一
                continue
            if name in seen and seen[name] != widget.lineno:
                duplicates.append(f"key={name!r} 同时出现在第 {seen[name]} 与 {widget.lineno} 行")
            seen[name] = widget.lineno
    assert not duplicates, "\n".join(duplicates)


def test_dynamic_widget_keys_are_namespaced(widgets: list[_WidgetCall]) -> None:
    """动态 key 必须以静态前缀+下划线开头，否则不同区域/不同控件之间可能撞名。

    ``key=f"sd_{name}"`` -> 前缀 ``sd_``（合格）；``key=f"{name}_box"`` -> 前缀为空（不合格）。
    静态 key（无 ``*``）不受本条约束。
    """

    bad = [
        f"app/streamlit_app.py:{w.lineno} key={sorted(w.span)}"
        for w in widgets
        if any("*" in name and not name.split("*")[0].endswith("_") for name in w.span)
    ]
    assert not bad, "动态 key 应以 '前缀_' 形式声明：\n" + "\n".join(bad)
