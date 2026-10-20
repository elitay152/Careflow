import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from careflow.public_export import archive_summary, export_public_snapshot, identifier, warehouse_evidence


def test_export_requires_synthetic_confirmation_before_any_database_access(tmp_path):
    engine = create_engine("sqlite://")
    with pytest.raises(ValueError, match="synthetic"):
        export_public_snapshot(engine, tmp_path / "public.json")
    assert not (tmp_path / "public.json").exists()
    engine.dispose()


@pytest.mark.parametrize("value", ['public; DROP SCHEMA public', 'x".patients--', '../data'])
def test_export_rejects_untrusted_schema_identifiers(value):
    with pytest.raises(ValueError, match="identifiers"):
        identifier(value)


def test_archive_copies_only_aggregate_allowlist(tmp_path):
    source = json.loads((Path(__file__).parents[1] / "docs/validation-results.json").read_text())
    source["secret"] = "private-value-must-not-export"
    source["database_counts"]["patient_ids"] = ["private-patient"]
    path = tmp_path / "archived.json"
    path.write_text(json.dumps(source))
    result = archive_summary(path, "2026-10-20")
    assert result["counts"]["patients"] == 108
    assert result["accepted"] == 14202
    assert "private" not in json.dumps(result)
    source["status"] = "failed"
    path.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="passed"):
        archive_summary(path, "2026-10-20")


def test_missing_build_evidence_is_unverified():
    result = warehouse_evidence(None, "careflow", "public", "analytics", None)
    assert result["status"] == "unverified"
    assert result["checks_passed"] is None


def evidence(tmp_path):
    manifest = {"metadata": {"invocation_id": "test-invocation"},
                "nodes": {"model.careflow.test": {"resource_type": "model", "database": "careflow", "schema": "analytics"}},
                "sources": {"source.careflow.raw": {"database": "careflow", "schema": "public"}}}
    results = {"metadata": {"invocation_id": "test-invocation", "invocation_started_at": "2026-10-20T01:00:00Z", "generated_at": "2026-10-20T01:01:00Z"},
               "results": [{"unique_id": "model.careflow.test", "status": "success"}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "run_results.json").write_text(json.dumps(results))
    return manifest, results


def test_partial_build_cannot_be_advertised_as_passed(tmp_path):
    evidence(tmp_path)
    assert warehouse_evidence(tmp_path, "careflow", "public", "analytics", None)["status"] == "failed"


def test_build_evidence_must_match_source_warehouse_and_ingestion(tmp_path):
    evidence(tmp_path)
    with pytest.raises(ValueError, match="another warehouse"):
        warehouse_evidence(tmp_path, "other_database", "public", "analytics", None)
    with pytest.raises(ValueError, match="source schema"):
        warehouse_evidence(tmp_path, "careflow", "other_raw", "analytics", None)
    with pytest.raises(ValueError, match="predates"):
        warehouse_evidence(tmp_path, "careflow", "public", "analytics", "2026-10-20T02:00:00Z")
    manifest, _ = evidence(tmp_path)
    manifest["metadata"]["invocation_id"] = "different-invocation"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="matching invocation"):
        warehouse_evidence(tmp_path, "careflow", "public", "analytics", None)
