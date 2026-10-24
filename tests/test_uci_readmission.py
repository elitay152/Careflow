from copy import deepcopy
import csv
import io
import json
import math

import pytest

from careflow.uci_readmission import (
    CATEGORICAL,
    NUMERIC,
    PAGE_URL,
    PROTOCOL_PATH,
    diagnosis_group,
    evaluate,
    metrics,
    parse_records,
    patient_split,
    validate_source,
)


def protocol():
    return json.loads(PROTOCOL_PATH.read_text())


def raw_table(rows):
    header = [
        "encounter_id",
        "patient_nbr",
        "readmitted",
        "diag_1",
        "diag_2",
        "diag_3",
        *NUMERIC,
        *(k for k in CATEGORICAL if not k.endswith("_group")),
    ]
    header += [f"unused_{i}" for i in range(50 - len(header))]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=header)
    writer.writeheader()
    for row in rows:
        base = {k: "1" for k in header}
        base.update(
            encounter_id="100",
            patient_nbr="10",
            readmitted="<30",
            diag_1="250.1",
            diag_2="V45",
            diag_3="?",
            discharge_disposition_id="1",
        )
        base.update(row)
        writer.writerow(base)
    return stream.getvalue().encode()


def test_source_identity_url_and_license_are_checked_before_data_fetch():
    meta = {
        "status": 200,
        "data": {
            "uci_id": 296,
            "num_instances": 101766,
            "data_url": "https://archive.ics.uci.edu/static/public/296/data.csv",
        },
    }
    page = b'<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>'
    assert validate_source(json.dumps(meta).encode(), page).endswith("/296/data.csv")
    for url in [
        "https://example.com/data.csv",
        "http://archive.ics.uci.edu/static/public/296/data.csv",
        "https://archive.ics.uci.edu/static/public/296/data.csv?token=secret",
        "https://archive.ics.uci.edu@evil.test/static/public/296/data.csv",
    ]:
        changed = deepcopy(meta)
        changed["data"]["data_url"] = url
        with pytest.raises(ValueError, match="allowlist"):
            validate_source(json.dumps(changed).encode(), page)
    with pytest.raises(ValueError, match="reuse"):
        validate_source(json.dumps(meta).encode(), b"No license here")


def test_intake_preserves_missingness_groups_patients_and_rejects_bad_input():
    p = {**protocol(), "expected_source_rows": 3}
    raw = raw_table(
        [
            {},
            {"encounter_id": "101", "readmitted": "NO", "num_lab_procedures": "?"},
            {"encounter_id": "102", "discharge_disposition_id": "11"},
        ]
    )
    rows, quality = parse_records(raw, p)
    assert quality["eligible_encounters"] == 2 and quality["excluded_encounters"] == 1
    assert rows[0]["split"] == rows[1]["split"] == patient_split("10")
    assert [r["outcome"] for r in rows] == [1, 0]
    assert math.isnan(rows[1]["features"]["num_lab_procedures"])
    assert (
        not {"patient_nbr", "encounter_id", "readmitted", "race"}
        & rows[0]["features"].keys()
    )
    for changed in (
        [{}, {}],
        [{"readmitted": "maybe"}, {"encounter_id": "101"}],
        [{"num_medications": "-1"}, {"encounter_id": "101"}],
    ):
        with pytest.raises(ValueError):
            parse_records(raw_table(changed), {**p, "expected_source_rows": 2})
    with pytest.raises(ValueError, match="Incomplete"):
        parse_records(raw, protocol())
    assert (
        diagnosis_group("250.9") == "diabetes"
        and diagnosis_group("401") == "circulatory"
    )
    assert diagnosis_group("E999") == "external" and diagnosis_group("?") == "missing"


def fixtures():
    rows = []
    for i in range(600):
        features = {k: float(i % 4) for k in NUMERIC}
        features.update({k: f"category-{i%3}" for k in CATEGORICAL})
        rows.append(
            {
                "patient": str(i),
                "split": patient_split(str(i)),
                "outcome": int(i % 5 == 0),
                "tie": str(i).zfill(8),
                "features": features,
            }
        )
    return rows


def test_holdout_outcomes_cannot_change_selection_preprocessing_or_ranking():
    p = protocol()
    before = evaluate(fixtures(), p)
    changed = deepcopy(fixtures())
    for row in changed:
        if row["split"] == "test":
            row["outcome"] = 1 - row["outcome"]
            row["features"]["age"] = "unseen category"
            row["features"]["num_lab_procedures"] = 1000000
    after = evaluate(changed, p)
    assert before["selected_model"] == after["selected_model"]
    assert before["patient_overlap"] == 0
    assert [r["validation"] for r in before["models"]] == [
        r["validation"] for r in after["models"]
    ]
    for r in before["models"]:
        test = r["test"]
        assert sum(b["n"] for b in test["calibration"]) == test["n"]
        assert sum(b["positives"] for b in test["calibration"]) == test["positives"]
        assert all(
            c["captured"] <= min(c["reviewed"], test["positives"])
            for c in test["review"]
        )
    duplicate = deepcopy(fixtures())
    duplicate[0]["split"] = "validation"
    duplicate.append({**duplicate[0], "split": "test"})
    with pytest.raises(ValueError, match="overlap"):
        evaluate(duplicate, p)


def test_ranking_ties_ignore_labels_and_calibration_endpoints_are_inclusive():
    rows = [{"outcome": i % 2, "tie": str(i).zfill(3)} for i in range(20)]
    m = metrics(rows, [0.0] * 10 + [1.0] * 10)
    assert m["calibration"][0]["n"] == m["calibration"][-1]["n"] == 10
    assert m["review"][0]["reviewed"] == 2 and m["review"][0]["captured"] == 1
    with pytest.raises(ValueError):
        metrics(rows, [float("nan")] * 20)
