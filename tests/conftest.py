from pathlib import Path

import pytest

from arxiv_radar.config import load_config

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg(tmp_path):
    c = load_config(FIX / "config.test.yaml")
    return c.model_copy(update={"data_dir": str(tmp_path / "data"), "cache_dir": str(tmp_path / "cache")})


@pytest.fixture
def sample_day():
    return FIX / "sample_day"
