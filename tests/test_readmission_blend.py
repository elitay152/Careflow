from copy import deepcopy

import pytest

from careflow.readmission_blend import WEIGHTS, experiment
from test_uci_readmission import fixtures, protocol


def test_blend_uses_only_training_rows_and_reports_fixed_candidates():
    rows = fixtures()
    result = experiment(rows, protocol())
    # Held-out labels/features must not even be accessed by development scoring.
    poisoned = [r if r["split"] == "train" else {"split": r["split"]} for r in rows]
    assert experiment(poisoned, protocol()) == result
    assert result["validation_scored"] is False and result["test_scored"] is False
    assert (
        sum(f["score_encounters"] for f in result["folds"])
        == result["training_encounters"]
    )
    assert [c["boosting_weight"] for c in result["candidates"]] == list(WEIGHTS)
    winner = max(result["candidates"], key=lambda c: c["metrics"]["average_precision"])
    assert result["selected_boosting_weight"] == winner["boosting_weight"]
    assert all(
        c["metrics"]["n"] == result["training_encounters"] for c in result["candidates"]
    )


def test_patient_folds_keep_repeated_encounters_together(monkeypatch):
    import careflow.uci_readmission as uci
    import numpy as np

    rows = [r for r in fixtures() if r["split"] == "train"]
    rows += deepcopy(rows[:40])
    observed = []

    class Spy:
        def fit(self, x, y):
            self.fit_ids = set(x[:, 0])

        def predict_proba(self, x):
            assert not self.fit_ids & set(x[:, 0])
            observed.extend(x[:, 0])
            return np.tile([0.8, 0.2], (len(x), 1))

    # Unique patient markers are test-only predictors for spying on leakage.
    for row in rows:
        row["features"][uci.NUMERIC[0]] = float(row["patient"])
    monkeypatch.setattr(uci, "make_estimator", lambda *args: Spy())
    result = experiment(rows, protocol())
    assert len(observed) == 2 * len(rows)
    assert result["selected_boosting_weight"] == 0


def test_insufficient_patients_fail_before_fitting():
    with pytest.raises(ValueError, match="five training patients"):
        experiment([], protocol())
