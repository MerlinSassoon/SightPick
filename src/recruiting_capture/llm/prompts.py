"""DeepSeek 视觉大模型提示词（事实确认 / 错字修正 / 结果去噪）。"""

from __future__ import annotations

import json
from typing import Sequence

DENOISE_SYSTEM = (
    "你是招聘页面文本整理助手。你会收到一段来自屏幕 OCR 的文本行列表，"
    "以及该区域的语义用途。请只依据给定文本整理，不得编造、不得改写原意。"
    "要求：1) 删除招聘者姓名、在线状态、App 按钮、收藏/举报/分享等界面噪声；"
    "2) 合并被 OCR 截断的同一句话；3) 去除滚动衔接造成的重复行；"
    "4) 保留职位描述正文、任职要求等有效内容；5) 严格输出 JSON。"
)

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
