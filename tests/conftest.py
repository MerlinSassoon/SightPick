"""pytest 公共夹具（兼做仓库根目录探测，避免测试名遮蔽标准库的 store 模块）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SAMPLES = ROOT / "data" / "samples"


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def samples_dir() -> Path:
    return SAMPLES


@pytest.fixture(scope="session")
def demo_plan():
    from recruiting_capture.models.plan_io import load_plan

    return load_plan(ROOT / "configs" / "demo_plan.json")


@pytest.fixture(scope="session")
def example_plan():
    from recruiting_capture.models.plan_io import load_plan

    return load_plan(ROOT / "configs" / "boss_1080p_example.json")


@pytest.fixture(scope="session")
def scenario() -> dict:
    return json.loads((SAMPLES / "demo_story.json").read_text(encoding="utf-8"))


@pytest.fixture
def no_sleep():
    return lambda seconds: None
