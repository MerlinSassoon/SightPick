"""DeepSeek 视觉大模型提示词（事实确认 / 错字修正 / 结果去噪）。"""

from __future__ import annotations

import json
from typing import Sequence

DENOISE_SYSTEM = (
    "你是页面文本整理助手。你会收到一段来自屏幕 OCR 的文本行列表，以及该区域的语义用途。请只依据给定文本整理，不得编造、不得改写原意。"
    "要求：1) 删除姓名、在线状态、App 按钮、收藏/举报/分享等界面噪声；"
    "2) 合并被 OCR 截断的同一句话；3) 去除滚动衔接造成的重复行；"
    "4) 按语义用途处理，语义用途可能同时包含标签类与描述类：标签类（如职位标签、关键词列表）把单行或列表中被拆散、重复的条目聚类还原为去重后的字符串，不同条目不得误合并；描述类（如职位描述、花卉描述）合并截断句但不跨段落硬拼、去重但不误删语义不同的相近段落；两类并存时分别处理、互不混淆；"
    "5) 保底：无论如何都保留该区域的核心有效内容（如职位描述正文、任职要求、花卉特征等）；6) 严格输出 JSON。"
)

#: 仅当区域用途含“标签”时追加：把标签聚成**一行**的显式契约 + few-shot。
#:
#: 为什么必须单独追加：通用 prompt 里“聚类还原为去重后的字符串”只是语义提示，
#: 与“不得改写原意”和扁平列表示例（每项一行完整文本）相冲突，实测模型会选择
#: 原样保留标签行。显式给出输出形状与示例后，同一份输入才能稳定聚成一行。
TAG_DENOISE_GUIDELINE = (
    "\n【标签聚类（本区域用途含“标签”，必须执行）】"
    "输入开头若出现一串短词（每个不超过 12 字、不含句读符号与数字编号），"
    "必须把它们合并为**一个** result 项，格式严格为“标签：A、B、C”，"
    "按出现顺序去重；不得让标签分散在多行，也不得改写词本身。"
    "并删除“职位描述”这类仅用于标识区域的标题行（它不是正文）。"
    '示例：输入 ["职位描述","人工智能","Ai","大模型","岗位职责：","负责模型测试。"] '
    '→ 输出 {"result": ["标签：人工智能、Ai、大模型", "岗位职责：", "负责模型测试。"]}'
)


def build_denoise_system(expected_content: str) -> str:
    """按区域用途组装 system prompt。

    只有用途里提到“标签”时才追加聚类契约；纯描述区域保持原 prompt，
    避免把正文开头的短句误判成标签。
    """

    if "标签" in (expected_content or ""):
        return DENOISE_SYSTEM + TAG_DENOISE_GUIDELINE
    return DENOISE_SYSTEM

ANCHOR_SYSTEM = (
    "你是招聘页面视觉确认助手。你会收到某个区域的截图与 OCR 文本行，"
    "需要判断其中是否存在给定的语义锚点（例如职位描述标题）。"
    "OCR 可能有错别字；若能确定是锚点的错字变体，请返回修正后的文本。"
    "不要臆测不存在的内容，严格输出 JSON。"
)

CLICK_SYSTEM = (
    "你是招聘页面状态确认助手。你会收到岗位列表区域的截图与 OCR 文本行。"
    "请判断当前鼠标点击是否命中了一个新的岗位列表项（右侧详情应随之切换）。"
    "若无法判断，matched 返回 false。严格输出 JSON。"
)


def _format_expectation(expected_format: str) -> str:
    return {
        "str": "一个完整的字符串",
        "list": "一个字符串列表，每个列表项是一行完整、独立的文本",
        "tuple": "一个字符串数组（调用方会转换为元组）",
    }.get(expected_format, "一个字符串列表")


def build_denoise_prompt(
    texts: Sequence[str],
    expected_content: str,
    expected_format: str,
) -> str:
    numbered = "\n".join(f"{index + 1}. {text}" for index, text in enumerate(texts))
    example = {
        "result": ["第一行完整文本", "第二行完整文本"]
        if expected_format != "str"
        else "完整的岗位名称"
    }
    return (
        f"区域用途（期望内容）：{expected_content or '未指定'}\n"
        f"期望输出：{_format_expectation(expected_format)}\n"
        f"OCR 文本行（共 {len(texts)} 行）：\n{numbered or '（空）'}\n\n"
        "请整理上述文本，并严格按以下 JSON 结构输出（不要输出解释）：\n"
        f"{json.dumps(example, ensure_ascii=False)}"
    )


def build_anchor_prompt(texts: Sequence[str], anchor: str) -> str:
    numbered = "\n".join(f"{index + 1}. {text}" for index, text in enumerate(texts))
    return (
        f"需要确认的锚点：{anchor}\n"
        f"OCR 文本行：\n{numbered or '（空）'}\n\n"
        "请判断锚点是否存在于这些文本中。若存在错字变体，请给出修正后的文本。"
        "严格输出 JSON："
        '{"exists": true, "corrected_key": "修正后的锚点文本或 null", '
        '"reason": "简要理由"}'
    )


def build_click_prompt(texts: Sequence[str]) -> str:
    numbered = "\n".join(f"{index + 1}. {text}" for index, text in enumerate(texts))
    return (
        "以下是点击后岗位列表区域的 OCR 文本行：\n"
        f"{numbered or '（空）'}\n\n"
        "请判断点击是否命中了新的岗位列表项。严格输出 JSON："
        '{"matched": true, "reason": "简要理由"}'
    )
