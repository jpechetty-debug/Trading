"""
test_models.py — Type-safe dataclass tests.
"""
import pytest
from src.models import Features, Order


class TestFeatures:
    def test_to_dict_keys(self):
        f = Features(rs_score=0.5, vol_ma_20=1_000_000)
        d = f.to_dict()
        assert "rs_score" in d
        assert "vol_ma_20" in d
        assert d["rs_score"] == 0.5
        assert len(d) == 10  # All 10 fields

    def test_defaults(self):
        f = Features()
        assert f.rs_score == 0.0
        assert f.market_regime == "Neutral"


class TestOrder:
    def test_to_dict_complete(self):
        o = Order(entry=100, stop=92, target=120, shares=50, risk_reward=2.5, valid_rr=True)
        d = o.to_dict()
        assert d["entry"] == 100
        assert d["valid_rr"] is True
        assert len(d) == 6


