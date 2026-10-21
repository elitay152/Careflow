from copy import deepcopy
import math

import pytest

from careflow.admission_diagnostics import (
    admission_mix,
    budget_threshold,
    episode_inventory,
    evaluate_fold,
    top_budget,
)
from careflow.hospitalization import FEATURES, make_snapshots, partition


def encounter(start, end, code, klass="IMP", system="http://snomed.info/sct"):
    return dict(
        patient_id="p",
        status="finished",
        started_at=start,
        ended_at=end,
        class_code=klass,
        class_system="http://terminology.hl7.org/CodeSystem/v3-ActCode",
        type_codes=[{"system": system, "code": code}],
        reason_codes=[],
    )


def test_episode_audit_merges_transfers_without_inferring_surgery_is_elective():
    patients = [{"id": "p", "birth_date": "1980-01-01"}]
    visits = [
        encounter("2020-01-01", "2020-01-03", "449411000124106"),
        encounter("2020-01-03", "2020-01-04", "32485007"),
        encounter("2019-12-31", "2020-01-01", "unclassified", "EMER"),
        encounter("2020-02-01", "2020-02-02", "183495009"),
        encounter("2020-03-01", "2020-03-02", "305408004"),
    ]
    episodes, summary = episode_inventory(patients, visits)
    assert len(episodes["p"]) == 3 and summary["adult_episodes_2015_2024"] == 3
    assert summary["episode_context_memberships"]["skilled_nursing"] == 1
    assert (
        summary["explicit_nonurgent_episodes"] == 1
        and summary["urgency_not_established_by_type"] == 2
    )
    assert summary["episodes_with_emergency_start_in_previous_24h"] == 1
    mix = admission_mix(
        [dict(patient_id="p", split="test", outcome=1, index_date="2019-12-31")],
        episodes,
    )
    assert (
        mix["test"]["positive_snapshots"] == 1
        and mix["test"]["context_memberships"]["surgical_department"] == 1
    )
    visits[0]["type_codes"][0]["system"] = "unrecognized"
    assert (
        "skilled_nursing"
        not in episode_inventory(patients, visits)[1]["episode_context_memberships"]
    )


def test_budget_is_per_snapshot_and_label_independent_with_ties():
    import numpy as np

    rows = [
        dict(patient_id=f"p{i}", index_date=date, outcome=i % 2)
        for date in ("2020-01-01", "2020-04-01")
        for i in range(39)
    ]
    probabilities = np.full(len(rows), 0.1)
    predicted = top_budget(rows, probabilities)
    assert sum(predicted) == 2
    for date in ("2020-01-01", "2020-04-01"):
        assert sum(
            p for r, p in zip(rows, predicted) if r["index_date"] == date
        ) <= math.floor(0.05 * 39)
    changed = deepcopy(rows)
    for row in changed:
        row["outcome"] = 1 - row["outcome"]
    assert top_budget(changed, probabilities) == predicted
    threshold = budget_threshold(probabilities)
    assert int((probabilities >= threshold).sum()) == 0
    assert sum(top_budget(rows[:19], probabilities[:19])) == 0


def test_f1_bootstrap_and_budget_thresholds_do_not_use_scoring_outcomes():
    groups = []
    for role, index in [
        ("train", "2020-10-01"),
        ("cal", "2022-10-01"),
        ("score", "2024-10-01"),
    ]:
        groups.append(
            [
                dict(
                    patient_id=f"{role}-{i}",
                    index_date=index,
                    outcome=int(i % 10 == 0),
                    features={f: 20 + i if f == "age_years" else 0 for f in FEATURES},
                )
                for i in range(100)
            ]
        )
    before = evaluate_fold(*groups, bootstrap=True)
    changed = deepcopy(groups)
    for row in changed[2]:
        row["outcome"] = 1 - row["outcome"]
    after = evaluate_fold(*changed, bootstrap=True)
    for a, b in zip(before["models"], after["models"], strict=True):
        assert a["threshold_stability"] == b["threshold_stability"]
        for policy in ("calibration_f1", "calibration_5pct_threshold"):
            assert (
                a["policies"][policy]["threshold"] == b["policies"][policy]["threshold"]
            )
    changed[2][0]["patient_id"] = changed[0][0]["patient_id"]
    with pytest.raises(ValueError, match="Patient overlap"):
        evaluate_fold(*changed)
    changed = deepcopy(groups)
    changed[0][-1]["index_date"] = "2023-01-01"
    with pytest.raises(ValueError, match="chronological"):
        evaluate_fold(*changed)


def test_all_periods_retains_history_without_changing_default_protocol():
    pid = next(f"p{i}" for i in range(100) if partition(f"p{i}") == "train")
    patients = [dict(id=pid, birth_date="1980-01-01", death_date=None, deceased=None)]
    visit = encounter("2014-01-01", "2014-01-02", "generic", "AMB")
    visit["patient_id"] = pid
    original, summary = make_snapshots(patients, [visit], [], "2025-01-01")
    history, _ = make_snapshots(patients, [visit], [], "2025-01-01", all_periods=True)
    assert len(history) > len(original) and any(
        r["index_date"].startswith("2022") for r in history
    )
    assert (
        make_snapshots(patients, [visit], [], "2025-01-01")[1]["input_sha256"]
        == summary["input_sha256"]
    )


def test_cutoff_attestation_and_empty_cohorts_are_explicit():
    from careflow.admission_diagnostics import validate_cutoff

    assert validate_cutoff("2025-01-01") == "2025-01-01"
    for value in ("2024-12-31", "20250101", "invalid"):
        with pytest.raises(ValueError):
            validate_cutoff(value)
    result = evaluate_fold([], [], [])
    assert result["status"] == "insufficient_data" and result["models"] == []
    assert result["roles"]["calibration"]["positive_patients"] == 0
