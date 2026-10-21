import pytest

from careflow.forecast import evaluate_forecast, predict, score, shift_month


def series(values):
    return [{"encounter_month": shift_month("2015-01-01", i), "encounter_count": value}
            for i, value in enumerate(values)]


def test_seasonal_baseline_repeats_matching_month_and_wins_ties():
    values = [100 + i % 12 for i in range(120)]
    result = evaluate_forecast(series(values))
    assert predict(values[:108], "seasonal_naive") == values[108:]
    assert result["evaluation"]["selected_model"] == "seasonal_naive"
    assert result["held_out_metrics"][0]["mae"] == 0
    assert result["predictions"] is None


def test_seasonal_trend_is_evaluated_then_refit_only_after_passing_gate():
    values = [100 + 3 * i + 5 * (i % 12) for i in range(120)]
    result = evaluate_forecast(series(values))
    assert result["evaluation"]["selected_model"] == "seasonal_trend"
    assert result["evaluation"]["selected_beats_baseline"]
    assert result["held_out_metrics"][2]["mae"] == pytest.approx(0, abs=1e-8)
    assert [row["value"] for row in result["predictions"]] == pytest.approx(
        [100 + 3 * i + 5 * (i % 12) for i in range(120, 132)])
    assert result["predictions"][0]["encounter_month"] == "2025-01-01"


def test_holdout_values_cannot_change_selection_or_test_predictions():
    original = [100 + 3 * i + i % 12 for i in range(120)]
    changed = original[:108] + [0] * 12
    first, second = (evaluate_forecast(series(values)) for values in (original, changed))
    a, b = first["evaluation"], second["evaluation"]
    assert a["selected_model"] == b["selected_model"]
    assert a["validation_metrics"] == b["validation_metrics"]
    for before, after in zip(a["test_predictions"], b["test_predictions"], strict=True):
        assert {key: value for key, value in before.items() if key != "actual"} == {
            key: value for key, value in after.items() if key != "actual"}
    assert a["input_sha256"] != b["input_sha256"]
    assert not b["selected_beats_baseline"]
    assert second["predictions"] is None


def test_missing_months_and_short_series_withhold_evaluation():
    rows = series([100] * 120)
    assert evaluate_forecast(rows[:-1])["status"] == "not_evaluated"
    rows[50]["encounter_month"] = rows[49]["encounter_month"]
    assert evaluate_forecast(rows)["evaluation"] is None


def test_zero_counts_have_undefined_wape_not_a_fabricated_percentage():
    result = evaluate_forecast(series([0] * 120))
    assert all(row["wape"] is None for row in result["held_out_metrics"])
    assert score([0, 0], [1, 1], "test")["mae"] == 1


def test_counts_must_be_nonnegative_integers():
    rows = series([100] * 120)
    rows[-1]["encounter_count"] = -1
    with pytest.raises(ValueError, match="counts"):
        evaluate_forecast(rows)
