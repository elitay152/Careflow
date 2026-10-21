import pytest

from careflow.condition_groups import catalog, condition_group


@pytest.mark.parametrize("code,group", [
    ("5251000175109", "social_context"),
    ("224299000", "social_context"),
    ("160903007", "social_context"),
    ("314529007", "care_context"),
    ("399211009", "history"),
    ("44054006", "clinical"),
    ("162864005", "clinical"),
])
def test_conditions_are_grouped_by_explicit_terminology_code(code, group):
    assert condition_group("http://snomed.info/sct", code) == group


def test_unmapped_codes_and_other_terminologies_are_not_assumed_clinical():
    assert condition_group("http://snomed.info/sct", "unknown") == "unclassified"
    assert condition_group("https://example.org/codes", "44054006") == "unclassified"


def test_catalog_groups_are_known_and_have_reviewable_labels():
    allowed = {"clinical", "social_context", "history", "care_context"}
    assert catalog()
    for code, entry in catalog().items():
        assert code.isdigit()
        assert entry["group"] in allowed
        assert entry["label"].strip()
