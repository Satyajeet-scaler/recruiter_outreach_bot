#!/usr/bin/env python3
"""Cron runner: pick lusha_recruiters with outreach_done=0, generate messages, send via LinkedIn.

Uses the same message-generation logic as ``run_sheet_pipeline.py``
(``generate_personalized_note`` from ``services.sheet_outreach.generate``).

Usage
-----
  python run_lusha_outreach.py
  python run_lusha_outreach.py --dry-run          # skip browser/send, print items
  python run_lusha_outreach.py --debug             # verbose screenshots
  python run_lusha_outreach.py --limit 5           # process at most 5 recruiters

Environment
-----------
  GEMINI_API_KEY
  GEMINI_MODEL  (default: gemini-2.5-flash)
  LINKEDIN_STORAGE_PATH / default data path
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Lusha recruiter outreach cron: generate & send LinkedIn messages.",
    )
    p.add_argument(
        "--storage-state-path",
        default=None,
        help="LinkedIn Playwright storage JSON (default: env or data/linkedin_storage.json).",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Pass debug=True into outreach (screenshots / verbose).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate messages but skip browser/send. Prints items to stdout.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of recruiters to process in this run.",
    )
    return p.parse_args()


def run_lusha_outreach(
    *,
    dry_run: bool = False,
    debug: bool = False,
    limit: int | None = None,
    storage_state_path: str | None = None,
) -> dict:
    """Core logic for the lusha outreach cron. Can be called from CLI or API."""
    from services.db.recruiter_store import get_pending_outreach_recruiters, mark_outreach_done
    from services.db.conversation_store import upsert_conversation, get_conversation_by_recruiter_id
    from services.db.message_store import save_message
    from services.db.pipeline_run_store import start_pipeline_run, finish_pipeline_run
    from services.db.models import (
        RecruiterConversation,
        ConversationMessage,
        OwnerType,
        DeliveryStatus,
    )
    from services.sheet_outreach.generate import generate_personalized_note

    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    # 1. Fetch pending recruiters
    pending = get_pending_outreach_recruiters()
    if limit:
        pending = pending[:limit]

    if not pending:
        logger.info("No pending lusha recruiters for outreach.")
        return {"ok": True, "total": 0, "message": "No pending recruiters."}

    logger.info("Found %d pending lusha recruiters for outreach.", len(pending))

    # 2. Start a pipeline run
    run_id = start_pipeline_run("outreach_batch")

    outreach_items: list[dict] = []
    errors: list[str] = []
    recruiter_map: dict[str, int] = {}  # profile_url -> recruiter_id

    # 3. Generate messages and create DB records for each recruiter
    for rec in pending:
        recruiter_id = rec["id"]
        linkedin_url = str(rec.get("linkedin_url") or "").strip()
        recruiter_name = str(rec.get("name") or rec.get("recruiter_name") or "").strip()
        job_url = str(rec.get("job_url") or "").strip()
        job_title = str(rec.get("job_title") or rec.get("title") or "").strip()
        company = str(rec.get("company") or "").strip()
        job_description = str(rec.get("job_description") or rec.get("description") or "").strip()

        if not linkedin_url:
            errors.append(f"Recruiter {recruiter_id}: missing linkedin_url, skipped.")
            logger.warning("Skipping recruiter %s: no linkedin_url.", recruiter_id)
            continue

        # 3a. Generate personalized outreach message (same as sheet pipeline)
        try:
            message_text = generate_personalized_note(
                recruiter_name=recruiter_name,
                job_description=job_description,
                job_title=job_title,
                company=company,
                max_chars=300,
                model_name=model_name,
            )
        except Exception as exc:
            err = f"Recruiter {recruiter_id} ({recruiter_name}): Gemini failed: {exc}"
            logger.exception("%s", err)
            errors.append(err)
            continue

        logger.info(
            "Generated message for recruiter %s (%s): %s",
            recruiter_id,
            recruiter_name,
            message_text[:80],
        )

        # 3b. Create/get recruiter_conversation
        db_convo = get_conversation_by_recruiter_id(recruiter_id)
        if not db_convo:
            db_convo = RecruiterConversation(
                recruiter_id=recruiter_id,
                channel="linkedin",
                campaign_name="lusha_outreach",
                conversation_context_json={
                    "recruiter_name": recruiter_name,
                    "linkedin_url": linkedin_url,
                    "job_title": job_title,
                    "company": company,
                    "source": "lusha_outreach_cron",
                },
            )
        db_convo.last_message_at = datetime.now()
        convo_id = upsert_conversation(db_convo)
        logger.info("Conversation upserted: recruiter_id=%s conversation_id=%s", recruiter_id, convo_id)

        # 3c. Save the outbound message record
        msg = ConversationMessage(
            conversation_id=convo_id,
            owner_type=OwnerType.RECRUITER_CONVERSATION,
            owner_id=convo_id,
            sender_type="bot",
            direction="outbound",
            content_text=message_text,
            delivery_status=DeliveryStatus.PENDING,
            context_source="lusha_outreach_cron",
            pipeline_run_id=run_id,
            message_context_json={
                "campaign": "lusha_outreach",
                "recruiter_id": recruiter_id,
                "job_title": job_title,
                "company": company,
            },
        )
        save_message(msg)

        outreach_items.append({"profile_url": linkedin_url, "message_text": message_text})
        recruiter_map[linkedin_url] = recruiter_id

    if not outreach_items:
        finish_pipeline_run(run_id, "completed", {
            "conversations_scanned": len(pending),
            "messages_processed": 0,
            "replies_sent": 0,
            "errors": errors,
        })
        return {"ok": True, "total": 0, "errors": errors, "message": "No items generated."}

    # 4. Dry-run: stop before browser
    if dry_run:
        logger.info("Dry-run mode: skipping LinkedIn send for %d items.", len(outreach_items))
        finish_pipeline_run(run_id, "dry_run", {
            "conversations_scanned": len(pending),
            "messages_processed": len(outreach_items),
            "replies_sent": 0,
            "errors": errors,
        })
        return {
            "ok": True,
            "dry_run": True,
            "total": len(outreach_items),
            "items": outreach_items,
            "errors": errors,
        }

    # 5. Send via LinkedIn outreach orchestrator (same as run_sheet_pipeline)
    from services.linkedin_recruiter import run_outreach_batch_sync

    kwargs: dict = {"debug": debug}
    if storage_state_path:
        kwargs["storage_state_path"] = storage_state_path
    elif not os.getenv("LINKEDIN_STORAGE_PATH"):
        local_default = _ROOT / "data" / "linkedin_storage.json"
        kwargs["storage_state_path"] = str(local_default)

    results = run_outreach_batch_sync(outreach_items, **kwargs)

    # 6. Mark outreach_done for successful sends
    success_count = 0
    for result in results:
        profile_url = result.get("profile_url", "")
        recruiter_id = recruiter_map.get(profile_url)
        if result.get("success") and recruiter_id:
            mark_outreach_done(recruiter_id)
            success_count += 1
            logger.info("Marked outreach_done for recruiter %s (%s)", recruiter_id, profile_url)

    finish_pipeline_run(run_id, "completed", {
        "conversations_scanned": len(pending),
        "messages_processed": len(outreach_items),
        "replies_sent": success_count,
        "errors": errors,
    })

    return {
        "ok": True,
        "total": len(results),
        "success_count": success_count,
        "failure_count": len(results) - success_count,
        "results": results,
        "errors": errors,
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()

    result = run_lusha_outreach(
        dry_run=args.dry_run,
        debug=args.debug,
        limit=args.limit,
        storage_state_path=args.storage_state_path,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

    if result.get("errors"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
