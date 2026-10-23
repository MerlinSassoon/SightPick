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
    __slots__ = ("lineno", "name", "keyword_args", "label", "enclosing_loop")

    def __init__(
        self,
        lineno: int,
        name: str,
        keyword_args: dict[str, ast.expr],
        label: str | None,
        enclosing_loop: bool,
    ) -> None:
        self.lineno = lineno
        self.name = name
        self.keyword_args = keyword_args
        self.label = label
        self.enclosing_loop = enclosing_loop

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


def _label_text(expr: ast.expr | None) -> str | None:
    """控件 label 的静态文本；动态拼接时返回 ``None``（视为“随区域变化”）。"""

    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
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


_LOOP_NODES = (ast.For, ast.AsyncFor, ast.While)


def _collect_widgets(tree: ast.AST) -> list[_WidgetCall]:
    """收集控件调用，并记录它是否位于循环体内、以及 label 的静态文本。"""

    found: list[_WidgetCall] = []

    def visit(node: ast.AST, in_loop: bool) -> None:
        for child in ast.iter_child_nodes(node):
            child_in_loop = in_loop or isinstance(child, _LOOP_NODES)
            name = _key_name(child)
            if name is not None:
                kwargs = {kw.arg: kw.value for kw in child.keywords if kw.arg is not None}
                label_node = (
                    child.args[0]
                    if child.args
                    else kwargs.get("label")
                )
                found.append(
                    _WidgetCall(
                        child.lineno,
                        name,
                        kwargs,
                        _label_text(label_node),
                        in_loop,
                    )
                )
            visit(child, child_in_loop)

    visit(tree, False)
    found.sort(key=lambda item: item.lineno)
    return found
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


def test_widget_labels_inside_loops_must_be_dynamic(widgets: list[_WidgetCall]) -> None:
    """循环体里“无 key”的控件，label 必须随循环变量变化。

    Streamlit 的元素 ID 由 ``(控件类型, label, options, index …)`` 计算；无 ``key`` 时，
    同一循环里两个 label 完全相同的控件会得到相同 ID，直接抛
    ``StreamlitDuplicateElementId``（去 key 改造后在配置页真实发生过）。

    带 ``key`` 的控件由 key 决定身份，不受本条约束（其唯一性由
    ``test_widget_keys_are_unique`` 负责）。
    """

    offenders = [
        f"  app/streamlit_app.py:{w.lineno}  st.{w.name}(label={w.label!r})"
        for w in widgets
        if w.enclosing_loop and w.label is not None and not w.span
    ]
    if offenders:
        pytest.fail(
            "以下控件位于循环体内、无 key 且 label 是固定字面量，多个实例会产生相同元素 ID：\n"
            + "\n".join(offenders)
            + "\n请把区域名等循环变量拼进 label（例如 f\"{name} 滚动距离\"）。",
            pytrace=False,
        )


def test_widget_labels_must_not_be_hidden(widgets: list[_WidgetCall]) -> None:
    """控件的 label 不得被隐藏：它是用户唯一能看到的字段说明。

    曾为“规避循环内 label 重复”而给控件加 ``label_visibility="collapsed"``，
    结果去 key 改造后界面上只剩一排没有任何说明的输入框，用户不知道该填什么。
    既然循环内的 label 已带上区域名前缀（见上一条测试），ID 不会冲突，
    就没有任何理由隐藏 label——这里把该约定锁死。
    """

    offenders = []
    for widget in widgets:
        expr = widget.keyword_args.get("label_visibility")
        if isinstance(expr, ast.Constant) and expr.value in ("collapsed", "hidden"):
            offenders.append(
                f"  app/streamlit_app.py:{widget.lineno}  st.{widget.name}("
                f"label={widget.label!r}, label_visibility={expr.value!r})"
            )
    if offenders:
        pytest.fail(
            "以下控件的 label 被隐藏，用户将看不到该填什么：\n"
            + "\n".join(offenders)
            + "\n请删除 label_visibility，改用带区域名前缀的可见 label。",
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
