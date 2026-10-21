from copy import deepcopy
from datetime import date, timedelta
import json

import pytest
from careflow.flu_forecast import (
    PROTOCOL_PATH,
    build_artifact,
    evaluate,
    parse_source,
    predict,
)


def protocol():
    return json.loads(PROTOCOL_PATH.read_text())


def history():
    start = date(2022, 2, 5)
    return [
        {"week": (start + timedelta(weeks=i)).isoformat(), "count": 100 + i % 13}
        for i in range(246)
    ]


def csv_bytes(rows):
    return (
        "date,location,location_name,value,weekly_rate\n"
        + "\n".join(",".join(row) for row in rows)
        + "\n"
    ).encode()


def test_source_validation_preserves_missing_states_and_rejects_bad_national_data():
    rows = [
        ["2022-02-05", "US", "United States", "10", "1"],
        ["2022-02-12", "US", "United States", "12", "1"],
        ["2022-02-05", "01", "Alabama", "", ""],
    ]
    series, quality = parse_source(csv_bytes(rows))
    assert [r["count"] for r in series] == [10, 12] and quality[
        "missing_state_values"
    ] == 1
    for changed in (
        rows + [rows[0]],
        [*rows[:1], ["2022-02-19", "US", "United States", "12", "1"]],
        [["2022-02-05", "US", "United States", "", ""], rows[1]],
        [["2022-02-05", "US", "United States", "NaN", ""], rows[1]],
        [["2022-02-05", "US", "United States", "-1", ""], rows[1]],
        [["2022-02-05", "US", "United States", "1.5", ""], rows[1]],
        [["2022-02-06", "US", "United States", "1", ""], rows[1]],
    ):
        with pytest.raises(ValueError):
            parse_source(csv_bytes(changed))
    with pytest.raises(ValueError, match="checksum"):
        build_artifact(csv_bytes(rows), protocol())


def test_models_use_correct_seasonal_lags_and_constant_series():
    p = protocol()
    rows = history()[:64]
    rows = [{**r, "count": i} for i, r in enumerate(rows)]
    assert predict(rows, "seasonal_naive", p) == [12.0, 13.0, 14.0, 15.0]
    constant = [{**r, "count": 10} for r in rows]
    for model in p["models"]:
        assert predict(constant, model, p) == pytest.approx([10] * 4)
    with pytest.raises(ValueError, match="insufficient"):
        predict(rows[:63], "last_value", p)


def test_selection_and_first_holdout_forecasts_do_not_use_holdout_outcomes():
    p = protocol()
    rows = history()
    before = evaluate(rows, p)
    changed = deepcopy(rows)
    for row in changed:
        if row["week"] >= p["holdout"]["start"]:
            row["count"] += 10000
    after = evaluate(changed, p)
    assert before["selected_model"] == after["selected_model"]
    assert before["validation"] == after["validation"]
    assert before["holdout"]["targets"] == 52 and before["holdout"]["forecasts"] == 208
    for a, b in zip(
        before["holdout"]["predictions"][:4],
        after["holdout"]["predictions"][:4],
        strict=True,
    ):
        assert all(a[model] == b[model] for model in p["models"])
    assert before["holdout"]["metrics"] != after["holdout"]["metrics"]
    for result in (before, after):
        selected = next(
            m["mae"]
            for m in result["holdout"]["metrics"]
            if m["model"] == result["selected_model"]
        )
        allowed = all(
            selected
            < next(m["mae"] for m in result["holdout"]["metrics"] if m["model"] == base)
            for base in ("last_value", "seasonal_naive")
        )
        assert (result["forecast"] is not None) == allowed
        assert (result["publication"]["status"] == "research_projection") == allowed


def test_incomplete_holdout_is_refused():
    with pytest.raises(ValueError, match="Incomplete"):
        evaluate(history()[:-40], protocol())
