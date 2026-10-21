import pytest

from careflow.demand import build_demand
from careflow.forecast import shift_month


def inputs(count=20):
    monthly = [{"encounter_month": shift_month("2015-01-01", i), "encounter_count": count} for i in range(120)]
    rows = [{**row, "care_setting": "ambulatory", "zip_area": "MA-021", "facility_id": "internal-id", "facility_name": "Example clinic"} for row in monthly]
    return monthly, rows


def test_facility_and_zip_are_separate_views_not_additive_and_do_not_expose_identifiers():
    monthly, rows = inputs()
    result = build_demand(monthly, rows, facility_count=1, inpatient_patients=0)
    assert len(result["groups"]) == 2
    assert result["coverage"]["finished_encounters"] == 2400
    for group in result["groups"]:
        assert group["annual_count"] == 240
        assert group["eligible"]
        assert group["forecast"]["predictions"] is None
        assert group["key"] != "internal-id"
        assert sum(group["counts"]) == 2400


def test_sparse_facilities_are_visible_but_never_scored_or_forecast():
    monthly, rows = inputs(1)
    result = build_demand(monthly, rows, facility_count=1, inpatient_patients=0)
    assert all(not g["eligible"] and g["forecast"] is None for g in result["groups"])


def test_missing_group_months_are_zero_recorded_events_only_when_global_month_exists():
    monthly, rows = inputs()
    rows[0]["facility_id"] = None
    rows[0]["facility_name"] = None
    rows[0]["zip_area"] = "unknown"
    result = build_demand(monthly, rows, facility_count=1, inpatient_patients=0)
    assert result["coverage"]["matched_provider_encounters"] == 2380
    assert result["groups"][0]["counts"][0] == 0
    rows.pop()
    with pytest.raises(ValueError, match="reconcile"):
        build_demand(monthly, rows, facility_count=1, inpatient_patients=0)


def test_missing_global_month_prevents_evaluation_even_with_high_volume():
    monthly, rows = inputs()
    monthly.pop(50); rows.pop(50)
    result = build_demand(monthly, rows, facility_count=1, inpatient_patients=0)
    assert all(not g["eligible"] for g in result["groups"])


def test_latest_year_uses_calendar_months_instead_of_twelve_observations():
    monthly, rows = inputs()
    monthly.pop(113); rows.pop(113)
    result = build_demand(monthly, rows, facility_count=1, inpatient_patients=0)
    assert result["coverage"]["annual_finished_encounters"] == 220
    assert all(group["annual_count"] == 220 and not group["eligible"] for group in result["groups"])
