"""UCI API ingestion and a fixed patient-grouped readmission benchmark."""

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = ROOT / "config/uci-readmission-protocol.json"
API_URL = "https://archive.ics.uci.edu/api/dataset?id=296"
PAGE_URL = "https://archive.ics.uci.edu/dataset/296/diabetes+130-us+hospitals+for+years+1999-2008"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
CACHE = ROOT / "data/external/uci296"
MAX_CSV = 64 * 1024 * 1024
SPLITS = ("train", "validation", "test")
NUMERIC = (
    "time_in_hospital",
    "num_lab_procedures",
    "num_procedures",
    "num_medications",
    "number_outpatient",
    "number_emergency",
    "number_inpatient",
    "number_diagnoses",
)
CATEGORICAL = (
    "age",
    "gender",
    "admission_type_id",
    "discharge_disposition_id",
    "admission_source_id",
    "max_glu_serum",
    "A1Cresult",
    "change",
    "diabetesMed",
    "insulin",
    "metformin",
    "diag_1_group",
    "diag_2_group",
    "diag_3_group",
)
LIMITATIONS = [
    "Older U.S. encounters involving patients with diabetes; not representative of all hospital patients or current practice.",
    "Recorded NO does not prove the absence of readmissions outside the source system; complete follow-up is not independently verified.",
    "No calendar admission dates or hospital identifiers support a chronological or independent-hospital test.",
    "Repeated encounters remain within a single patient split; encounter-level metrics are correlated and have no estimated confidence intervals.",
    "Features describe the index encounter at discharge, not an admission-time prediction. Diagnosis groups are not a validated severity score.",
    "This is a retrospective research benchmark, not clinical validation, an individual patient scoring service, or a claim of preventable readmissions.",
]


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected redirect from UCI; source must be reviewed")


def fetch(url, limit):
    request = Request(url, headers={"User-Agent": "Careflow-UCI-benchmark/1.0"})
    with build_opener(NoRedirect).open(request, timeout=30) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("UCI response exceeds size limit")
    return raw


def validate_source(metadata_raw, license_raw):
    if len(metadata_raw) > 2 * 1024 * 1024 or len(license_raw) > 4 * 1024 * 1024:
        raise ValueError("UCI metadata exceeds size limit")
    envelope = json.loads(metadata_raw)
    metadata = envelope.get("data", {})
    if (
        envelope.get("status") != 200
        or metadata.get("uci_id") != 296
        or metadata.get("num_instances") != 101766
    ):
        raise ValueError("Unexpected UCI dataset identity or row count")
    url = metadata.get("data_url", "")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "archive.ics.uci.edu"
        or parsed.path != "/static/public/296/data.csv"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("UCI data URL is outside the fixed dataset allowlist")
    license_html = license_raw.decode("utf-8").lower()
    if "creativecommons.org/licenses/by/4.0" not in license_html:
        raise ValueError(
            "UCI dataset page does not establish the expected CC BY 4.0 reuse basis"
        )
    return url


def missing_value(value):
    return value.strip().casefold() in {"", "?", "nan", "na", "n/a", "null"}


def diagnosis_group(value):
    value = value.strip()
    if missing_value(value) or value.casefold() == "none":
        return "missing"
    if value.upper().startswith("V"):
        return "supplemental"
    if value.upper().startswith("E"):
        return "external"
    try:
        code = float(value)
    except ValueError:
        raise ValueError(f"Unrecognized ICD-9 diagnosis token: {value[:32]!r}") from None
    if not math.isfinite(code) or code < 0:
        raise ValueError(f"Invalid ICD-9 diagnosis token: {value[:32]!r}")
    if 250 <= code < 251:
        return "diabetes"
    for low, high, label in (
        (390, 460, "circulatory"),
        (460, 520, "respiratory"),
        (520, 580, "digestive"),
        (580, 630, "genitourinary"),
        (800, 1000, "injury"),
        (140, 240, "neoplasm"),
    ):
        if low <= code < high:
            return label
    return "other"


def patient_split(patient):
    bucket = (
        int.from_bytes(
            hashlib.sha256(("uci296-patient-v1:" + patient).encode()).digest()[:8],
            "big",
        )
        % 100
    )
    return "train" if bucket < 70 else "validation" if bucket < 85 else "test"


def parse_records(raw, protocol):
    if len(raw) > MAX_CSV:
        raise ValueError("UCI CSV exceeds size limit")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    required = {
        "encounter_id",
        "patient_nbr",
        "readmitted",
        "diag_1",
        "diag_2",
        "diag_3",
        *NUMERIC,
        *(k for k in CATEGORICAL if not k.endswith("_group")),
    }
    if (
        not reader.fieldnames
        or len(reader.fieldnames) != 50
        or len(set(reader.fieldnames)) != 50
        or not required <= set(reader.fieldnames)
    ):
        raise ValueError("Unexpected UCI columns")
    rows, seen, patients, excluded = [], set(), set(), Counter()
    labels = Counter()
    source_rows = 0
    for row in reader:
        source_rows += 1
        if (
            source_rows > protocol["expected_source_rows"]
            or None in row
            or None in row.values()
        ):
            raise ValueError("Malformed or oversized UCI table")
        ids = []
        for key in ("encounter_id", "patient_nbr"):
            value = row[key]
            if not value.isascii() or not value.isdigit():
                raise ValueError("Invalid UCI identifier")
            ids.append(str(int(value)))
        encounter, patient = ids
        if encounter in seen:
            raise ValueError("Duplicate encounter identifier")
        seen.add(encounter)
        patients.add(patient)
        target = row["readmitted"]
        if target not in ("<30", ">30", "NO"):
            raise ValueError("Unexpected readmission label")
        labels[target] += 1
        try:
            disposition = int(row["discharge_disposition_id"])
        except ValueError:
            raise ValueError("Invalid discharge disposition") from None
        if disposition in protocol["excluded_discharge_codes"]:
            excluded[str(disposition)] += 1
            continue
        features = {}
        for key in NUMERIC:
            value = row[key]
            number = math.nan if missing_value(value) else float(value)
            if not math.isnan(number) and (
                not math.isfinite(number) or number < 0 or not number.is_integer()
            ):
                raise ValueError("Invalid numeric feature")
            features[key] = number
        for key in CATEGORICAL:
            if key.endswith("_group"):
                features[key] = diagnosis_group(row[key.removesuffix("_group")])
            else:
                value = row[key]
                features[key] = (
                    "missing"
                    if missing_value(value)
                    or value.strip().casefold() == "unknown/invalid"
                    else value
                )
                if len(features[key]) > 100:
                    raise ValueError("Oversized categorical value")
        rows.append(
            {
                "patient": patient,
                "split": patient_split(patient),
                "outcome": int(target == "<30"),
                "tie": hashlib.sha256(
                    ("uci296-rank-v1:" + encounter).encode()
                ).hexdigest(),
                "features": features,
            }
        )
    if source_rows != protocol["expected_source_rows"]:
        raise ValueError("Incomplete UCI dataset")
    quality = {
        "source_rows": source_rows,
        "source_patients": len(patients),
        "eligible_encounters": len(rows),
        "eligible_patients": len({r["patient"] for r in rows}),
        "excluded_encounters": sum(excluded.values()),
        "excluded_by_disposition": {
            str(k): excluded[str(k)] for k in protocol["excluded_discharge_codes"]
        },
        "source_labels": dict(labels),
        "duplicate_encounters": 0,
    }
    return rows, quality


def metrics(rows, probabilities):
    import numpy as np
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

    y = np.asarray([r["outcome"] for r in rows])
    p = np.asarray(probabilities)
    if (
        len(p) != len(rows)
        or not np.isfinite(p).all()
        or ((p < 0) | (p > 1)).any()
        or set(y.tolist()) != {0, 1}
    ):
        raise ValueError("Evaluation requires finite probabilities and both outcomes")
    n, positives = len(y), int(y.sum())
    calibration = []
    for i in range(10):
        mask = (p >= i / 10) & ((p <= 1) if i == 9 else (p < (i + 1) / 10))
        count = int(mask.sum())
        calibration.append(
            {
                "lower": i / 10,
                "upper": (i + 1) / 10,
                "n": count,
                "positives": int(y[mask].sum()),
                "mean_probability": float(p[mask].mean()) if count else None,
                "observed_rate": float(y[mask].mean()) if count else None,
            }
        )
    order = sorted(range(n), key=lambda i: (-float(p[i]), rows[i]["tie"]))
    capacities = []
    for capacity in (0.1, 0.2):
        reviewed = math.ceil(n * capacity)
        captured = int(y[order[:reviewed]].sum())
        capacities.append(
            {
                "capacity": capacity,
                "reviewed": reviewed,
                "captured": captured,
                "precision": captured / reviewed,
                "recall": captured / positives,
            }
        )
    return {
        "n": n,
        "positives": positives,
        "prevalence": positives / n,
        "average_precision": float(average_precision_score(y, p)),
        "roc_auc": float(roc_auc_score(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "calibration": calibration,
        "review": capacities,
    }


def evaluate(rows, protocol):
    import numpy as np
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    from threadpoolctl import threadpool_limits
    import warnings

    parts = {name: [r for r in rows if r["split"] == name] for name in SPLITS}
    ids = {name: {r["patient"] for r in part} for name, part in parts.items()}
    if any(
        ids[a] & ids[b]
        for a, b in (("train", "validation"), ("train", "test"), ("validation", "test"))
    ):
        raise ValueError("Patients overlap across splits")
    if any(
        len(part) < 20 or {r["outcome"] for r in part} != {0, 1}
        for part in parts.values()
    ):
        raise ValueError(
            "Each patient-disjoint split requires both outcomes and sufficient samples"
        )
    if any(len({r["features"][key] for r in rows}) > 64 for key in CATEGORICAL):
        raise ValueError(
            "Unexpected categorical cardinality; review UCI source before fitting"
        )
    names = [*NUMERIC, *CATEGORICAL]
    x = {
        s: np.asarray(
            [[r["features"][key] for key in names] for r in part], dtype=object
        )
        for s, part in parts.items()
    }
    y = {s: np.asarray([r["outcome"] for r in part]) for s, part in parts.items()}
    prior_index = NUMERIC.index("number_inpatient")
    prior_flags = np.nan_to_num(x["train"][:, prior_index].astype(float), nan=0) > 0
    prior_rates = {
        v: (int(y["train"][prior_flags == v].sum()) + 1)
        / (int((prior_flags == v).sum()) + 2)
        for v in (False, True)
    }
    results = []
    test_probabilities = {}
    for model in protocol["models"]:
        if model in ("logistic", "boosting"):
            prep = ColumnTransformer(
                [
                    (
                        "numeric",
                        make_pipeline(
                            SimpleImputer(strategy="median", keep_empty_features=True),
                            StandardScaler(),
                        ),
                        list(range(len(NUMERIC))),
                    ),
                    (
                        "categorical",
                        OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                        list(range(len(NUMERIC), len(names))),
                    ),
                ]
            )
            estimator = (
                LogisticRegression(**protocol["logistic"])
                if model == "logistic"
                else HistGradientBoostingClassifier(**protocol["boosting"])
            )
            fitted = make_pipeline(prep, estimator)
            with threadpool_limits(limits=1), warnings.catch_warnings():
                warnings.simplefilter("error", ConvergenceWarning)
                fitted.fit(x["train"], y["train"])

        def probabilities(split):
            if model == "prevalence":
                return np.full(len(parts[split]), float(y["train"].mean()))
            if model == "prior_inpatient":
                flags = np.nan_to_num(x[split][:, prior_index].astype(float), nan=0) > 0
                return np.asarray([prior_rates[bool(v)] for v in flags])
            with threadpool_limits(limits=1):
                return fitted.predict_proba(x[split])[:, 1]

        results.append(
            {
                "model": model,
                "validation": metrics(parts["validation"], probabilities("validation")),
                "test": None,
            }
        )
        test_probabilities[model] = probabilities("test")
    selected = max(results, key=lambda r: r["validation"]["average_precision"])["model"]
    for result in results:
        result["test"] = metrics(parts["test"], test_probabilities[result["model"]])
    splits = [
        {
            "split": s,
            "encounters": len(parts[s]),
            "patients": len(ids[s]),
            "positives": int(y[s].sum()),
        }
        for s in SPLITS
    ]
    return {
        "selected_model": selected,
        "splits": splits,
        "patient_overlap": 0,
        "models": results,
    }


def atomic_write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staged:
        temporary = Path(staged.name)
        staged.write(raw)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def build_artifact(raw, metadata_raw, license_raw, expected_sha256=None):
    data_url = validate_source(metadata_raw, license_raw)
    sha = digest(raw)
    if expected_sha256 and sha != expected_sha256:
        raise ValueError("UCI source differs from the expected SHA-256")
    protocol = json.loads(PROTOCOL_PATH.read_text())
    rows, quality = parse_records(raw, protocol)
    evaluation = evaluate(rows, protocol)
    from importlib.metadata import version

    return {
        "schema_version": 1,
        "status": "completed",
        "dataset_kind": "observed_public_aggregate",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": protocol,
        "protocol_sha256": digest(canonical(protocol)),
        "source": {
            "dataset_id": 296,
            "name": "Diabetes 130-US Hospitals for Years 1999–2008",
            "api_url": API_URL,
            "data_url": data_url,
            "page_url": PAGE_URL,
            "license_url": LICENSE_URL,
            "license_evidence": "CC BY 4.0 link checked on the official dataset page at retrieval",
            "attribution": "UCI Machine Learning Repository, Diabetes 130-US Hospitals for Years 1999–2008; Strack et al. (2014). Changes: eligibility filtering, patient-grouped split, feature transformation, and aggregate model evaluation. No endorsement implied.",
            "csv_sha256": sha,
            "metadata_sha256": digest(metadata_raw),
            "license_page_sha256": digest(license_raw),
            "source_pinned": bool(expected_sha256),
        },
        "software": {"scikit_learn": version("scikit-learn")},
        "quality": quality,
        "evaluation": evaluation,
        "limitations": LIMITATIONS,
    }


def read_bounded(path, limit):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Cached UCI evidence exceeds size limit")
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument(
        "--expected-sha256", default=os.getenv("UCI_READMISSION_SHA256")
    )
    args = parser.parse_args()
    if not CACHE.resolve().is_relative_to(ROOT / "data/external"):
        raise ValueError("UCI cache must remain under ignored local data")
    if args.download:
        metadata = fetch(API_URL, 2 * 1024 * 1024)
        license_page = fetch(PAGE_URL, 4 * 1024 * 1024)
        url = validate_source(metadata, license_page)
        raw = fetch(url, MAX_CSV)
    else:
        metadata = read_bounded(CACHE / "metadata.json", 2 * 1024 * 1024)
        license_page = read_bounded(CACHE / "license-page.html", 4 * 1024 * 1024)
        with (CACHE / "data.csv").open("rb") as stream:
            raw = stream.read(MAX_CSV + 1)
    artifact = build_artifact(raw, metadata, license_page, args.expected_sha256)
    if args.download:
        for name, content in (
            ("metadata.json", metadata),
            ("license-page.html", license_page),
            ("data.csv", raw),
        ):
            atomic_write(CACHE / name, content)
    output = ROOT / "data/public/readmission.json"
    if not output.resolve().is_relative_to(ROOT / "data/public"):
        raise ValueError("UCI export must remain under ignored public-export directory")
    atomic_write(
        output, json.dumps(artifact, indent=2, allow_nan=False).encode() + b"\n"
    )
    print(
        "UCI readmission benchmark completed. Only aggregate evidence written to data/public/readmission.json."
    )


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        raise SystemExit(
            f"UCI request failed with HTTP {error.code}; no new benchmark was published. No automatic retry."
        ) from None
    except URLError:
        raise SystemExit(
            "UCI connection unavailable; no new benchmark was published. No automatic retry."
        ) from None
