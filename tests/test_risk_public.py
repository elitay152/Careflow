from copy import deepcopy
import json

import pytest

from careflow.hospitalization import make_snapshots
from careflow.risk_public import load_public_risk, validate_public_risk
from test_hospitalization import cohort


def evidence():
    _,summary=make_snapshots(*cohort(),"2025-01-01")
    summary['source_loaded_at']='2026-10-21T00:00:00+00:00'
    return summary


def test_risk_public_evidence_is_an_explicit_aggregate_schema():
    summary=evidence()
    assert validate_public_risk(summary)==summary
    for mutate in [lambda d:d.update(patient_ids=['sentinel']),lambda d:d['splits'][0].update(patient_id='sentinel'),lambda d:d['features'].append('patient_id'),lambda d:d.update(input_sha256='api-key'),lambda d:d['exclusions'].update(notes='sentinel')]:
        changed=deepcopy(summary);mutate(changed)
        with pytest.raises(ValueError):validate_public_risk(changed)


def test_stale_or_wrong_cohort_artifacts_are_withheld_and_private_metadata_is_not_exported(tmp_path):
    summary=evidence();summary['_source']={'database':'careflow','schema':'cohort'}
    summary['private_rows']=[{'patient_id':'sentinel'}]
    path=tmp_path/'summary.json';path.write_text(json.dumps(summary))
    public=load_public_risk(path,None,'careflow','cohort',summary['source_loaded_at'])
    assert '_source' not in public and 'private_rows' not in public
    assert 'sentinel' not in json.dumps(public)
    assert load_public_risk(path,None,'careflow','other',summary['source_loaded_at']) is None
    assert load_public_risk(path,None,'careflow','cohort','2026-10-22T00:00:00+00:00') is None
