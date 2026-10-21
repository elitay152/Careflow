from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from careflow.api import create_app
from careflow.assistant import Assistant, Plan
from careflow.assistant import BaselinePlanner
from careflow.budget import LedgerUnavailable, Limits, UsageLedger, UsageLimitReached
from careflow.llm import OpenAIPlanner, PlannerUnavailable


def planner(tmp_path, handler, limits=None):
    ledger = UsageLedger(tmp_path / "usage.sqlite3", limits=limits)
    return OpenAIPlanner("test-only-secret", transport=httpx.MockTransport(handler), ledger=ledger)


def response(plan=None, usage=None, status="completed"):
    return httpx.Response(200, json={"status": status, "usage": usage or {"input_tokens":100,"output_tokens":20},
        "output": [{"type":"message", "content":[{"type":"output_text", "text": json.dumps(plan or Plan(metric="unsupported").model_dump())}]}]})


def test_budget_survives_restart_and_pending_request(tmp_path):
    path = tmp_path / "usage.sqlite3"
    ledger = UsageLedger(path, limits=Limits(daily_requests=1))
    ledger.reserve("gpt-4.1-mini", 100)
    restarted = UsageLedger(path, limits=Limits(daily_requests=1))
    with pytest.raises(UsageLimitReached):
        restarted.reserve("gpt-4.1-mini", 100)
    assert restarted.snapshot()["attempt_statuses"] == {"pending": 1}


def test_daily_reset_does_not_reset_lifetime(tmp_path):
    current = [datetime(2026,1,1,12,tzinfo=timezone.utc)]
    ledger = UsageLedger(tmp_path / "usage.sqlite3", Limits(daily_requests=1,lifetime_requests=2), clock=lambda:current[0])
    ledger.reserve("gpt-4.1-mini", 100)
    with pytest.raises(UsageLimitReached):
        ledger.reserve("gpt-4.1-mini", 100)
    current[0] += timedelta(days=1)
    ledger.reserve("gpt-4.1-mini", 100)
    current[0] += timedelta(days=1)
    with pytest.raises(UsageLimitReached):
        ledger.reserve("gpt-4.1-mini", 100)
    assert ledger.snapshot()["lifetime"]["remaining_requests"] == 0


def test_concurrent_processes_cannot_overspend(tmp_path):
    path = tmp_path / "usage.sqlite3"
    UsageLedger(path, Limits(daily_requests=2))
    code = '''
import sys
from datetime import datetime,timezone
from careflow.budget import UsageLedger,Limits,UsageLimitReached
ledger=UsageLedger(sys.argv[1],Limits(daily_requests=2),clock=lambda:datetime(2026,1,1,tzinfo=timezone.utc))
try:
    ledger.reserve('gpt-4.1-mini',100)
    print('allowed')
except UsageLimitReached:
    print('blocked')
'''
    def run(_):
        return subprocess.run([sys.executable,"-c",code,str(path)], check=True,capture_output=True,text=True).stdout.strip()
    with ThreadPoolExecutor(max_workers=6) as executor:
        outcomes = list(executor.map(run, range(6)))
    assert outcomes.count("allowed") == 2
    assert outcomes.count("blocked") == 4


def test_token_budget_is_reserved_and_never_refunded(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.sqlite3", Limits(daily_tokens=400))
    identifier = ledger.reserve("gpt-4.1-mini", 100)  # 100 input + 256 output.
    ledger.finish(identifier, True, {"input_tokens":10,"output_tokens":5})
    assert ledger.snapshot()["daily"]["reserved_tokens"] == 356
    with pytest.raises(UsageLimitReached):
        ledger.reserve("gpt-4.1-mini", 100)


@pytest.mark.parametrize("failure", ["timeout", "http429", "incomplete", "invalid_plan"])
def test_failed_provider_calls_remain_charged_without_retry(tmp_path, failure):
    calls = []
    def handler(request):
        calls.append(request)
        assert json.loads(request.content)["max_output_tokens"] == 256
        if failure == "timeout":
            raise httpx.ReadTimeout("test timeout")
        if failure == "http429":
            return httpx.Response(429)
        if failure == "incomplete":
            return response(status="incomplete")
        return response(plan={"metric":"bad_metric"})
    p = planner(tmp_path, handler, Limits(daily_requests=1))
    with pytest.raises(PlannerUnavailable):
        p.plan("monthly encounters", [])
    with pytest.raises(UsageLimitReached):
        p.plan("monthly encounters", [])
    assert len(calls) == 1
    assert p.ledger.snapshot()["attempt_statuses"] == {"failed":1}
    assert p.ledger.snapshot()["daily"]["reserved_tokens"] > 256
    if failure in {"incomplete","invalid_plan"}:
        assert p.ledger.snapshot()["reported_tokens"] == {"input_tokens":100,"output_tokens":20}


def test_model_and_input_limits_block_before_http(tmp_path):
    calls = []
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    with pytest.raises(ValueError,match="allowlist"):
        OpenAIPlanner("test",model="expensive-model",ledger=ledger)
    p = OpenAIPlanner("test",ledger=ledger,transport=httpx.MockTransport(lambda req:calls.append(req)))
    with pytest.raises(ValueError):
        p.plan("x" * 1001, [])
    with pytest.raises(ValueError,match="input-size"):
        p.plan("monthly encounters", [{"definition":"x" * 20_000}])
    assert calls == []
    assert ledger.snapshot()["lifetime"]["attempts"] == 0


def test_provider_accounting_overrun_halts_future_calls(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return response(usage={"input_tokens":100_000,"output_tokens":20})
    p = planner(tmp_path, handler)
    with pytest.raises(UsageLimitReached):
        p.plan("monthly encounters", [])
    restarted = OpenAIPlanner("test",ledger=UsageLedger(tmp_path / "usage.sqlite3"),transport=p.transport)
    with pytest.raises(UsageLimitReached):
        restarted.plan("monthly encounters", [])
    assert len(calls) == 1
    assert restarted.ledger.snapshot()["halted"] is True


def test_unavailable_ledger_blocks_http(tmp_path):
    p = planner(tmp_path,lambda req:pytest.fail("HTTP must not run"))
    with p.ledger.transaction() as connection:
        connection.execute("DROP TABLE attempts")
    with pytest.raises(LedgerUnavailable):
        p.plan("monthly encounters", [])


def test_api_budget_limit_is_429_without_model_or_database_query(tmp_path):
    p = planner(tmp_path,lambda req:pytest.fail("HTTP must not run"),Limits(daily_requests=1))
    p.ledger.reserve("gpt-4.1-mini", 100)
    engine = create_engine("sqlite://")  # Any warehouse query would fail.
    with TestClient(create_app(Assistant(engine,p)), base_url="http://localhost") as client:
        usage = client.get("/usage").json()
        assert usage["daily"]["remaining_requests"] == 0
        answer = client.post("/ask",json={"question":"monthly encounters"})
        assert answer.status_code == 429
        assert "test-only-secret" not in answer.text
    engine.dispose()


def test_limits_can_be_lowered_but_not_raised_via_environment(monkeypatch):
    monkeypatch.setenv("CAREFLOW_LLM_DAILY_REQUEST_LIMIT","1")
    assert Limits.from_env().daily_requests == 1
    monkeypatch.setenv("CAREFLOW_LLM_DAILY_REQUEST_LIMIT","500")
    with pytest.raises(ValueError):
        Limits.from_env()


def test_ledger_does_not_store_prompt_or_key(tmp_path):
    p = planner(tmp_path,lambda req:response())
    p.plan("private-question-marker", [])
    with p.ledger.transaction() as connection:
        dump = "\n".join(connection.iterdump())
    assert "private-question-marker" not in dump
    assert "test-only-secret" not in dump


def test_baseline_does_not_create_or_consume_model_ledger(tmp_path,monkeypatch):
    ledger_path=tmp_path / "not-created.sqlite3"
    monkeypatch.setattr("careflow.llm.DEFAULT_LEDGER_PATH",ledger_path)
    engine=create_engine("sqlite://")
    with TestClient(create_app(Assistant(engine,BaselinePlanner())), base_url="http://localhost") as client:
        assert client.get("/usage").json()["model_calls_enabled"] is False
        assert client.post("/ask",json={"question":"List patient names"}).json()["status"] == "unsupported"
    assert not ledger_path.exists()
    engine.dispose()


def test_corrupt_ledger_fails_closed(tmp_path):
    path=tmp_path / "corrupt.sqlite3"
    path.write_text("not a database")
    with pytest.raises(LedgerUnavailable):
        UsageLedger(path)


def test_live_evaluation_batch_is_refused_before_any_request(tmp_path,monkeypatch):
    from careflow import evaluate
    p=planner(tmp_path,lambda request:pytest.fail("HTTP must not run"))
    engine=create_engine("sqlite://")
    monkeypatch.setattr(evaluate,"from_settings",lambda **kwargs:Assistant(engine,p))
    monkeypatch.setattr(sys,"argv",["evaluate","--mode","llm","--suite","all","--output",str(tmp_path / "results.json")])
    with pytest.raises(SystemExit) as error:
        evaluate.main()
    assert error.value.code == 2
    assert p.ledger.snapshot()["lifetime"]["attempts"] == 0
    assert not (tmp_path / "results.json").exists()
