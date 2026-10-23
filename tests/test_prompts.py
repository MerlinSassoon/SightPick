"""去噪提示词的组装规则。

背景：``DENOISE_SYSTEM`` 是所有区域共用的 system prompt，其中“标签聚类”只是一句
语义提示，且与“不得改写原意” + 扁平列表示例相冲突——实测模型会把标签原样保留成
多行。因此标签契约改为**按区域用途动态追加**，本文件守住这个开关：

- 用途含“标签” → system prompt 里必须出现“标签：”的显式格式与示例；
- 纯描述用途 → 不得出现标签规则（否则正文开头的短句可能被误并成标签）。
"""

from __future__ import annotations

from recruiting_capture.llm.prompts import (
    DENOISE_SYSTEM,
    TAG_DENOISE_GUIDELINE,
    build_denoise_system,
)


def test_tag_usage_appends_explicit_contract() -> None:
    system = build_denoise_system("职位标签和职位描述")

    assert system.startswith(DENOISE_SYSTEM)
    assert TAG_DENOISE_GUIDELINE in system
    # 必须是可执行的输出形状，而不是“聚类还原为字符串”这种模糊表述
    assert "标签：A、B、C" in system
    assert "人工智能、Ai、大模型" in system  # few-shot 示例
    assert "不得让标签分散在多行" in system
    assert "删除“职位描述”这类" in system


def test_description_only_usage_keeps_base_prompt() -> None:
    system = build_denoise_system("职位描述")

    assert system == DENOISE_SYSTEM
    assert "标签：" not in system


def test_empty_usage_keeps_base_prompt() -> None:
    assert build_denoise_system("") == DENOISE_SYSTEM
    assert build_denoise_system(None) == DENOISE_SYSTEM  # type: ignore[arg-type]
