from contextlib import asynccontextmanager
import os
from importlib.resources import files

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from .assistant import Assistant, BaselinePlanner, CATALOG
from .llm import OpenAIPlanner, PlannerUnavailable
from .budget import LedgerUnavailable, UsageLimitReached


def from_settings(database_url=None, mode=None):
    url = database_url or os.getenv("CAREFLOW_ASSISTANT_DATABASE_URL")
    if not url:
        raise ValueError("Configure CAREFLOW_ASSISTANT_DATABASE_URL with the local read-only warehouse role")
    if make_url(url).get_backend_name() != "postgresql":
        raise ValueError("The assistant requires PostgreSQL")
    mode = mode or os.getenv("CAREFLOW_ASSISTANT_MODE", "baseline")
    if mode == "llm":
        planner = OpenAIPlanner(os.getenv("CAREFLOW_LLM_API_KEY"), os.getenv("CAREFLOW_LLM_MODEL", "gpt-4.1-mini"))
    elif mode == "baseline":
        planner = BaselinePlanner()
    else:
        raise ValueError("CAREFLOW_ASSISTANT_MODE must be baseline or llm")
    engine = create_engine(url, pool_pre_ping=True)
    return Assistant(engine, planner, schema=os.getenv("CAREFLOW_ANALYTICS_SCHEMA", "analytics"))


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=1000)

    @field_validator("question")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Question cannot be blank")
        return value


def create_app(assistant=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.assistant = assistant or from_settings()
        try:
            yield
        finally:
            if assistant is None:
                app.state.assistant.engine.dispose()

    app = FastAPI(title="Careflow analytics assistant", version="0.3.0", lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        return files("careflow").joinpath("assistant_ui.html").read_text()

    @app.get("/health")
    def health():
        try:
            app.state.assistant.health()
        except SQLAlchemyError:
            raise HTTPException(503, "Warehouse unavailable") from None
        planner = app.state.assistant.planner
        return {"status": "ok", "mode": "llm" if isinstance(planner, OpenAIPlanner) else "baseline",
                "model_connectivity": "not_checked" if isinstance(planner, OpenAIPlanner) else "not_used",
                "scope": "synthetic healthcare analytics"}

    @app.get("/metrics")
    def metrics():
        return CATALOG

    @app.get("/usage")
    def usage():
        planner = app.state.assistant.planner
        if not isinstance(planner, OpenAIPlanner):
            return {"mode": "baseline", "model_calls_enabled": False}
        try:
            return {"mode": "llm", "model_calls_enabled": True, **planner.ledger.snapshot()}
        except LedgerUnavailable:
            raise HTTPException(503, "Usage ledger unavailable; model calls are disabled") from None

    @app.post("/ask")
    def ask(request: Question):
        try:
            return app.state.assistant.ask(request.question)
        except UsageLimitReached:
            raise HTTPException(429, "Model usage is paused by local limits. Switch to baseline mode or review usage.") from None
        except LedgerUnavailable:
            raise HTTPException(503, "Usage ledger unavailable; model calls are disabled") from None
        except PlannerUnavailable:
            raise HTTPException(502, "Model planning unavailable; no query was executed") from None
        except SQLAlchemyError:
            raise HTTPException(503, "Warehouse query unavailable") from None
        except ValueError:
            raise HTTPException(422, "Invalid question or plan") from None

    return app
