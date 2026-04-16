"""Railway web entrypoint for recruiter outreach bot."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from services.linkedin_recruiter import run_outreach_batch_sync
from services.linkedin_session import (
    get_linkedin_storage_path,
    save_linkedin_storage_state_json,
)

app = FastAPI(
    title="Recruiter Outreach Bot API",
    description="Internal API for running LinkedIn outreach batches.",
    version="1.0.0",
)

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    level_name = os.getenv("APP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        )
    else:
        root.setLevel(level)


def _validate_internal_trigger_token(internal_token: str | None) -> None:
    expected_token = os.getenv("INTERNAL_TRIGGER_TOKEN")
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="INTERNAL_TRIGGER_TOKEN is not configured on server.",
        )
    if not internal_token or internal_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized internal trigger.",
        )


class OutreachItem(BaseModel):
    profile_url: str = Field(..., min_length=1)
    message_text: str = Field(..., min_length=1)


class OutreachRequest(BaseModel):
    items: list[OutreachItem]
    debug: bool = False
    timeout_s: int = 25
    initial_wait_s: float = 4.0
    wait_before_close_s: float = 10.0
    storage_state_path: str | None = None


@app.on_event("startup")
def _on_startup() -> None:
    _configure_logging()
    logger.info("startup complete")


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "recruiter-outreach-bot", "status": "running"}


@app.get("/internal/linkedin-session")
def linkedin_session_status(
    x_internal_trigger_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _validate_internal_trigger_token(x_internal_trigger_token)
    path = get_linkedin_storage_path()
    return {
        "exists": path.is_file(),
        "storage_path": str(path),
    }


@app.post("/internal/linkedin-session")
def upload_linkedin_session(
    payload: dict[str, Any],
    x_internal_trigger_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _validate_internal_trigger_token(x_internal_trigger_token)
    try:
        path = save_linkedin_storage_state_json(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "storage_path": str(path),
        "cookie_count": len(payload.get("cookies", [])),
    }


@app.post("/internal/run-outreach")
def run_outreach(
    payload: OutreachRequest,
    x_internal_trigger_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _validate_internal_trigger_token(x_internal_trigger_token)
    if not payload.items:
        raise HTTPException(status_code=400, detail="items must be a non-empty list")

    logger.info("internal outreach run requested item_count=%s", len(payload.items))
    kwargs: dict[str, Any] = {
        "debug": payload.debug,
        "timeout_s": payload.timeout_s,
        "initial_wait_s": payload.initial_wait_s,
        "wait_before_close_s": payload.wait_before_close_s,
    }
    if payload.storage_state_path:
        kwargs["storage_state_path"] = payload.storage_state_path

    items = [item.model_dump() for item in payload.items]
    results = run_outreach_batch_sync(items, **kwargs)
    success_count = sum(1 for row in results if row.get("success"))
    return {
        "ok": True,
        "total": len(results),
        "success_count": success_count,
        "failure_count": len(results) - success_count,
        "results": results,
    }
