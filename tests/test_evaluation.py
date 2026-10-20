import json

import pytest

from careflow.analytics import ROOT
from careflow.assistant import BaselinePlanner, Plan, retrieve
from careflow.evaluate import evaluate, matches


def test_core_evaluation_plans_and_retrieval():
    cases = json.loads((ROOT / "evaluation" / "assistant_cases.json").read_text())
    assert len({case["id"] for case in cases}) == len(cases)
    planner = BaselinePlanner()
    for case in cases:
        if case["suite"] != "core":
            continue
        expected = Plan.model_validate(case["expected_plan"])
        assert planner.plan(case["question"], retrieve(case["question"])).plan == expected, case["id"]
        if expected.metric != "unsupported":
            assert expected.metric in {item["id"] for item in retrieve(case["question"])}, case["id"]


def test_evaluation_rejects_empty_and_distinguishes_missing_rows():
    with pytest.raises(ValueError, match="empty"):
        evaluate(None, [])
    assert not matches([{"value": 3}], [])
    assert not matches({"value": 3}, {"value": 4})
    assert matches({"fraction": 2 / 3}, {"fraction": .6666666666667, "source": "test"})


def test_evaluation_stops_on_budget_and_marks_remaining_unrun():
    from careflow.budget import UsageLimitReached
    class Exhausted:
        calls=0
        def ask(self,question):
            self.calls+=1
            raise UsageLimitReached("test budget")
    assistant=Exhausted()
    cases=[{"id":str(index),"question":"monthly encounters","expected_plan":{"metric":"monthly_encounters"}} for index in range(3)]
    result=evaluate(assistant,cases)
    assert assistant.calls == 1
    assert result["failed"] == 1
    assert result["unrun"] == 2
