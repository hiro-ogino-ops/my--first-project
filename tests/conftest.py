from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from note_shop.config import load_settings

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """テスト用に、出力先とDBを tmp に逃がした会社設定。"""
    monkeypatch.setenv("NOTESHOP_OFFLINE", "1")
    base = load_settings(REPO_ROOT / "config" / "company.yaml")
    inbox = tmp_path / "inbox"
    shutil.copytree(REPO_ROOT / "examples" / "inbox", inbox)

    research = base.staff.research
    object.__setattr__(research, "manual", {"inbox": str(inbox)})
    object.__setattr__(base, "out_dir", tmp_path / "out")
    object.__setattr__(base, "db_path", tmp_path / "noteshop.db")
    object.__setattr__(base.staff.writing, "letter_dir", str(REPO_ROOT / "examples" / "letters"))
    return base


@pytest.fixture
def company(settings):
    from note_shop.staff import Company

    with Company(settings) as c:
        yield c
