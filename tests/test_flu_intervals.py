from copy import deepcopy
from datetime import date, timedelta
import json
import math

import pytest

from careflow.flu_forecast import PROTOCOL_PATH
from careflow.flu_intervals import build_intervals, interval_bounds, interval_metrics


def test_bounds_round_outward_clip_and_score_misses():
    assert interval_bounds(2.5, 4.2) == (0, 7)
    assert interval_bounds(10.2, 2.1) == (8, 13)
    rows = [{"actual": a, "model": 10} for a in (5, 8, 10, 12, 15)]
    metrics = interval_metrics(rows, "model", 2, 80)
    assert metrics["coverage"] == 3 / 5  # Endpoints are included.
    assert metrics["below"] == metrics["above"] == 1
    assert metrics["mean_width"] == 4
    assert metrics["mean_interval_score"] == pytest.approx(16)
    assert interval_metrics([], "model", 0, 80)["coverage"] is None
    for bad in (-1, math.inf, math.nan):
        with pytest.raises(ValueError):
            interval_bounds(bad, 1)


def test_radii_ignore_all_holdout_outcomes_and_late_validation_outcomes():
    p = json.loads(PROTOCOL_PATH.read_text())
    validation = []
    for window in p["validation"]:
        d = date.fromisoformat(window["start"])
        while d.isoformat() <= window["end"]:
            for h in p["horizons"]:
                validation.append(
                    {
                        "week": d.isoformat(),
                        "horizon": h,
                        "actual": 100,
                        **{
                            m: 100 + h * (len(validation) // 4 + 1) for m in p["models"]
                        },
                    }
                )
            d += timedelta(weeks=1)
    holdout = [
        {
            "week": "2025-07-05",
            "horizon": h,
            "actual": 100,
            **{m: 100 for m in p["models"]},
        }
        for h in p["horizons"]
    ]
    before = build_intervals(validation, holdout, p)
    changed = deepcopy(validation)
    for r in changed:
        if r["week"] > "2025-06-07":
            r["actual"] = 10**8
    after = build_intervals(changed, [{**r, "actual": 99999} for r in holdout], p)
    for a, b in zip(before["by_horizon"], after["by_horizon"], strict=True):
        assert a["calibration_n"] == b["calibration_n"] == 102
        assert a["quantile_ranks"] == [
            {"level": 80, "rank": 83},
            {"level": 95, "rank": 98},
        ]
        for am, bm in zip(a["models"], b["models"], strict=True):
            assert [x["radius"] for x in am["bands"]] == [
                x["radius"] for x in bm["bands"]
            ]
            assert am["bands"][0]["radius"] <= am["bands"][1]["radius"]
            assert am["bands"][0]["metrics"]["coverage"] == 1
            assert bm["bands"][0]["metrics"]["coverage"] == 0
    with pytest.raises(ValueError, match="overlaps"):
        build_intervals(validation, holdout, {**p, "holdout": {"start": "2025-06-07"}})
    with pytest.raises(ValueError, match="Insufficient"):
        build_intervals(validation[:4], holdout, p)
