"""Grounded metric planning with fixed SQL templates and read-only transactions."""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from importlib.resources import files
import json
import re
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import text


CATALOG = json.loads(files("careflow").joinpath("metric_catalog.json").read_text())
METRICS = {item["id"]: item for item in CATALOG}
MONTHLY = {"monthly_encounters", "monthly_patients", "mean_encounter_duration"}
ROW_LIMIT = 50


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    metric: Literal["monthly_encounters", "monthly_patients", "mean_encounter_duration", "condition_prevalence", "medication_requests", "unsupported"]
    start_month: str | None = None
    end_month: str | None = None
    condition_filter: str | None = None

    @field_validator("start_month", "end_month")
    @classmethod
    def valid_month(cls, value):
        if value is not None:
            if not re.fullmatch(r"\d{4}-\d{2}-01", value):
                raise ValueError("Month boundaries must be YYYY-MM-01")
            date.fromisoformat(value)
        return value

    @field_validator("condition_filter")
    @classmethod
    def valid_filter(cls, value):
        if value is not None and (not value.strip() or len(value) > 100):
            raise ValueError("Condition filter must contain 1–100 characters")
        return value

    @model_validator(mode="after")
    def valid_combination(self):
        if self.metric not in MONTHLY and (self.start_month or self.end_month):
            raise ValueError("Date filters are only supported for monthly metrics")
        if self.condition_filter is not None and self.metric != "condition_prevalence":
            raise ValueError("Condition filters require condition_prevalence")
        if self.start_month and self.end_month and self.start_month >= self.end_month:
            raise ValueError("start_month must precede exclusive end_month")
        return self


def retrieve(question, limit=3):
    words = set(re.findall(r"[a-z0-9]+", question.lower()))
    scored = []
    for item in CATALOG:
        terms = set(re.findall(r"[a-z0-9]+", " ".join((item["title"], item["definition"], item["keywords"])).lower()))
        scored.append((len(words & terms), item))
    return [item for _, item in sorted(scored, key=lambda row: (-row[0], row[1]["id"]))[:limit]]


@dataclass
class Planned:
    plan: Plan
    mode: str
    model: str | None = None
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})


def next_month(month):
    parsed = date.fromisoformat(month + "-01")
    return date(parsed.year + (parsed.month == 12), parsed.month % 12 + 1, 1).isoformat()


class BaselinePlanner:
    """A deliberately bounded grammar, for offline checks; not an LLM."""
    def plan(self, question, context):
        question = question.lower().strip().rstrip("?.!")
        patterns = {
            "monthly_encounters": r"(?:monthly (?:finished )?encounters|how many (?:finished )?encounters (?:were there )?(?:by|per) month)",
            "monthly_patients": r"(?:monthly (?:distinct |unique )?patients|(?:distinct|unique) patients by month)",
            "mean_encounter_duration": r"(?:average|mean) encounter duration by month",
            "medication_requests": r"(?:medication|prescription) requests by status",
            "condition_prevalence": r"(?:recorded )?condition prevalence(?: for (.+))?",
        }
        dates = {}
        match = re.search(r" in (\d{4}-\d{2})$", question)
        interval = re.search(r" from (\d{4}-\d{2}) to (\d{4}-\d{2})$", question)
        try:
            if match:
                dates = {"start_month": match[1] + "-01", "end_month": next_month(match[1])}
                question = question[:match.start()]
            elif interval:
                dates = {"start_month": interval[1] + "-01", "end_month": next_month(interval[2])}
                question = question[:interval.start()]
            for metric, pattern in patterns.items():
                match = re.fullmatch(pattern, question)
                if match:
                    condition = match[1] if metric == "condition_prevalence" else None
                    if condition and re.search(r"\b(by|women|men|age|female|male|ignore|select|drop|delete)\b", condition):
                        break
                    return Planned(Plan(metric=metric, condition_filter=condition, **dates), "baseline")
        except ValueError:
            pass
        return Planned(Plan(metric="unsupported"), "baseline")


def compile_query(plan, schema):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise ValueError("Invalid analytics schema")
    if plan.metric not in METRICS:
        raise ValueError("No SQL for unsupported questions")
    metric = METRICS[plan.metric]
    relation = f'"{schema}"."{metric["relation"]}"'
    params = {"row_limit": ROW_LIMIT + 1}
    if plan.metric in MONTHLY:
        sql = f"""SELECT encounter_month, {metric['column']} AS value FROM {relation}
WHERE (CAST(:start_month AS DATE) IS NULL OR encounter_month >= CAST(:start_month AS DATE))
  AND (CAST(:end_month AS DATE) IS NULL OR encounter_month < CAST(:end_month AS DATE))
ORDER BY encounter_month DESC LIMIT :row_limit"""
        params.update(start_month=plan.start_month, end_month=plan.end_month)
    elif plan.metric == "condition_prevalence":
        sql = f"""SELECT code_system, code, condition_name, affected_patients, total_patients, prevalence_fraction
FROM {relation}
WHERE (CAST(:condition_filter AS TEXT) IS NULL OR position(lower(CAST(:condition_filter AS TEXT)) in lower(condition_name)) > 0)
ORDER BY affected_patients DESC, code_system, code LIMIT :row_limit"""
        params["condition_filter"] = plan.condition_filter
    else:
        sql = f"SELECT status, count(*) AS request_count FROM {relation} GROUP BY status ORDER BY status LIMIT :row_limit"
    return sql, params


@contextmanager
def readonly(engine):
    if engine.dialect.name != "postgresql":
        raise ValueError("The assistant requires a PostgreSQL warehouse")
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout = '3000ms'"))
            connection.execute(text("SET LOCAL lock_timeout = '1000ms'"))
            yield connection


def json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


class Assistant:
    def __init__(self, engine, planner, schema="analytics"):
        compile_query(Plan(metric="monthly_encounters"), schema)
        self.engine, self.planner, self.schema = engine, planner, schema

    def health(self):
        sql, params = compile_query(Plan(metric="monthly_encounters"), self.schema)
        with readonly(self.engine) as connection:
            connection.execute(text(sql), params).fetchone()

    def ask(self, question):
        if not isinstance(question, str) or not question.strip() or len(question) > 1000:
            raise ValueError("Question must contain 1–1000 characters")
        started = perf_counter()
        context = retrieve(question)
        planned = self.planner.plan(question, context)
        planning_ms = (perf_counter() - started) * 1000
        result = {
            "status": "unsupported", "mode": planned.mode, "model": planned.model,
            "plan": planned.plan.model_dump(), "rows": [], "truncated": False,
            "citations": [], "query": None, "usage": planned.usage,
            "estimated_cost_usd": None,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        query_ms = 0.0
        if planned.plan.metric == "unsupported":
            result["answer"] = "I could not map this question to a supported metric and filter. Ask about monthly encounters, monthly patients, average encounter duration, recorded condition prevalence, or medication requests by status."
        else:
            metric = METRICS[planned.plan.metric]
            sql, params = compile_query(planned.plan, self.schema)
            query_started = perf_counter()
            with readonly(self.engine) as connection:
                data = connection.execute(text(sql), params).mappings().all()
            query_ms = (perf_counter() - query_started) * 1000
            rows = [{key: json_value(value) for key, value in row.items()} for row in data[:ROW_LIMIT]]
            result.update(status="answered", rows=rows, truncated=len(data) > ROW_LIMIT,
                          query={"sql": sql, "parameters": params})
            result["citations"] = [{
                "metric_id": metric["id"], "title": metric["title"],
                "definition": metric["definition"], "relation": f"{self.schema}.{metric['relation']}",
                "limitations": metric["limitations"],
            }]
            if not rows:
                result["answer"] = f"No matching rows were found for {metric['title'].lower()}. This does not establish a zero population count."
            elif planned.plan.metric in MONTHLY:
                first = rows[0]
                units = "minutes" if planned.plan.metric == "mean_encounter_duration" else ("patients" if planned.plan.metric == "monthly_patients" else "encounters")
                value = "unavailable" if first["value"] is None else f"{first['value']:,.2f}".rstrip("0").rstrip(".")
                result["answer"] = f"{metric['title']}: {len(rows)} month(s) returned, newest first. Most recent displayed month: {first['encounter_month'][:7]}, {value} {units}."
            elif planned.plan.metric == "condition_prevalence":
                if len(rows) == 1:
                    first = rows[0]
                    result["answer"] = f"{first['condition_name']}: {first['affected_patients']} of {first['total_patients']} loaded patients ({100 * first['prevalence_fraction']:.1f}%)."
                else:
                    result["answer"] = f"{len(rows)} distinct coded condition groups returned. Each group's prevalence is shown separately; patients can appear in multiple groups, so the counts must not be added together."
                    if planned.plan.condition_filter:
                        result["answer"] += " The name filter is a literal substring match and can include related conditions; use a more specific name to narrow it."
            else:
                result["answer"] = f"{sum(row['request_count'] for row in rows)} medication requests across {len(rows)} status group(s). Requests do not establish dispensing or adherence."
            if result["truncated"]:
                result["answer"] += f" Results are limited to {ROW_LIMIT} rows; narrow the question to see a smaller range."
        result["timing_ms"] = {"planner": round(planning_ms, 2), "query": round(query_ms, 2),
                               "total": round((perf_counter() - started) * 1000, 2)}
        return result
