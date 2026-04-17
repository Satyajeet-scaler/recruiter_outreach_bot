"""Read recruiter + role_relevant tabs, join on job_url, generate messages via Gemini."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, TypedDict

from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

RECRUITERS_TAB_RE = re.compile(r"^role_recruiters_info_(.+)_(\d{4}-\d{2}-\d{2})$")
JD_MAX_CHARS = 8000


class OutreachItemDict(TypedDict):
    profile_url: str
    message_text: str


def items_for_outreach_json(items: list[OutreachItemDict]) -> list[dict[str, str]]:
    """Strict ``run_outreach.py`` / JSON shape: LinkedIn ``profile_url`` and ``message_text`` only."""
    return [
        {"profile_url": x["profile_url"], "message_text": x["message_text"]}
        for x in items
    ]


@dataclass
class GenerateOutreachResult:
    run_date: date
    items: list[OutreachItemDict]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def normalize_job_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    return u.rstrip("/")


def parse_recruiters_tab_title(title: str) -> tuple[str, str] | None:
    """Return (role_slug, date_str YYYY-MM-DD) if title matches pattern."""
    m = RECRUITERS_TAB_RE.match(title.strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def role_relevant_tab_name(role_slug: str, date_str: str) -> str:
    return f"role_relevant_{role_slug}_{date_str}"


def _header_index_map(header_row: list[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, cell in enumerate(header_row):
        key = str(cell).strip().lower()
        if key and key not in out:
            out[key] = i
    return out


def _row_dict(values: list[Any], col_map: dict[str, int]) -> dict[str, str]:
    row: dict[str, str] = {}
    for name, idx in col_map.items():
        if idx < len(values):
            row[name] = str(values[idx]).strip() if values[idx] is not None else ""
        else:
            row[name] = ""
    return row


def build_jd_index(rows: list[list[Any]], *, warnings: list[str]) -> dict[str, str]:
    """First row is header; map normalized job_url -> description (first wins)."""
    if not rows:
        return {}
    col_map = _header_index_map(rows[0])
    if "job_url" not in col_map:
        warnings.append("role_relevant sheet missing 'job_url' column; no JDs loaded.")
        return {}
    if "description" not in col_map:
        warnings.append("role_relevant sheet missing 'description' column; no JDs loaded.")
        return {}

    index: dict[str, str] = {}
    for r in rows[1:]:
        rd = _row_dict(r, col_map)
        ju = normalize_job_url(rd.get("job_url", ""))
        if not ju:
            continue
        if ju in index:
            continue
        desc = rd.get("description", "")
        index[ju] = desc[:JD_MAX_CHARS]
    return index


def get_sheets_credentials(
    *,
    credentials_path: str | None = None,
    service_account_info: dict[str, Any] | None = None,
) -> Credentials:
    """Resolve Google service account credentials for read-only Sheets access.

    Precedence: explicit ``service_account_info`` → explicit ``credentials_path`` →
    env ``GOOGLE_SERVICE_ACCOUNT_JSON`` (raw JSON string) → env
    ``GOOGLE_APPLICATION_CREDENTIALS`` (file path).
    """
    scopes = ("https://www.googleapis.com/auth/spreadsheets.readonly",)
    if service_account_info is not None:
        return Credentials.from_service_account_info(service_account_info, scopes=scopes)
    if credentials_path:
        return Credentials.from_service_account_file(credentials_path, scopes=scopes)
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw and raw.strip():
        info = json.loads(raw)
        return Credentials.from_service_account_info(info, scopes=scopes)
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if path:
        return Credentials.from_service_account_file(path, scopes=scopes)
    raise ValueError(
        "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS, "
        "or pass credentials_path / service_account_info.",
    )


def open_spreadsheet(credentials: Credentials, spreadsheet_id: str):
    import gspread

    gc = gspread.authorize(credentials)
    return gc.open_by_key(spreadsheet_id)


def _get_gemini_model(model_name: str):
    import google.generativeai as genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model_name)


def generate_personalized_note(
    *,
    recruiter_name: str,
    job_description: str,
    job_title: str,
    company: str,
    max_chars: int,
    model_name: str,
) -> str:
    """Scaler LinkedIn connection note via Gemini; enforce max length with hard truncate."""
    model = _get_gemini_model(model_name)
    name_part = recruiter_name.strip() or "there"
    jd = (job_description or "").strip()
    if len(jd) > JD_MAX_CHARS:
        jd = jd[:JD_MAX_CHARS]
    title_hint = (job_title or "").strip()
    company_hint = (company or "").strip()
    optional_hints = ""
    if title_hint or company_hint:
        optional_hints = (
            f"\nOptional spreadsheet hints (use only if consistent with the JD): "
            f"title={title_hint or 'n/a'}, company={company_hint or 'n/a'}."
        )

    # Scaler LinkedIn outreach note template.
    prompt = f"""You are writing a LinkedIn connection request note on behalf of someone at Scaler — an upskilling platform with a community of working professionals seeking career growth.

Inputs:
- Recipient name: {name_part}
- Job description:
---
{jd}
---{optional_hints}

Instructions:
1. Extract the role, company name, and 1-2 key skills from the JD
2. Write a LinkedIn connection note under {max_chars} characters (including spaces)
3. Mention the recipient's name
4. Reference the role and company
5. Mention Scaler has professionals with the relevant skills
6. Clarify Scaler is NOT a staffing or recruitment agency — it's a community of upskilling professionals
7. No fees charged
8. End with a soft CTA to review profiles
9. Warm, concise, human tone

Return only the note. No explanation, no preamble."""
    response = model.generate_content(prompt)
    text = ""
    if response and getattr(response, "text", None):
        text = response.text.strip()
    elif response and getattr(response, "candidates", None):
        parts = []
        for c in response.candidates:
            if not c.content or not c.content.parts:
                continue
            for p in c.content.parts:
                if hasattr(p, "text") and p.text:
                    parts.append(p.text)
        text = "".join(parts).strip()

    if not text:
        raise RuntimeError("Gemini returned empty text")

    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


def generate_outreach_items(
    *,
    spreadsheet_id: str,
    credentials: Credentials,
    run_date: date,
    model_name: str | None = None,
    max_message_chars: int = 300,
    dry_run: bool = False,
) -> GenerateOutreachResult:
    """
    For all tabs ``role_recruiters_info_<slug>_<run_date>``, rows with non-empty
    ``recruiter_profile_url``, join ``job_url`` to ``role_relevant_<slug>_<run_date>``
    for ``description``, then generate a personalized note via Gemini.
    """
    date_str = run_date.isoformat()
    model = model_name or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    warnings: list[str] = []
    errors: list[str] = []
    items: list[OutreachItemDict] = []

    workbook = open_spreadsheet(credentials, spreadsheet_id)

    matching_tabs: list[tuple[str, str]] = []
    for ws in workbook.worksheets():
        parsed = parse_recruiters_tab_title(ws.title)
        if not parsed:
            continue
        slug, tab_date = parsed
        if tab_date == date_str:
            matching_tabs.append((ws.title, slug))

    if not matching_tabs:
        warnings.append(f"No worksheets matching role_recruiters_info_*_{date_str} found.")

    for tab_title, role_slug in matching_tabs:
        rel_name = role_relevant_tab_name(role_slug, date_str)
        try:
            rel_ws = workbook.worksheet(rel_name)
        except Exception:
            warnings.append(
                f"Tab [{tab_title}]: missing companion sheet {rel_name!r}; skipping this tab."
            )
            continue

        jd_rows = rel_ws.get_all_values()
        jd_index = build_jd_index(jd_rows, warnings=warnings)

        info_ws = workbook.worksheet(tab_title)
        rows = info_ws.get_all_values()
        if not rows:
            continue
        col_map = _header_index_map(rows[0])
        required = ("job_url", "recruiter_name", "recruiter_profile_url")
        missing_req = [req for req in required if req not in col_map]
        if missing_req:
            errors.append(f"Tab [{tab_title}]: missing columns {missing_req!r}")
            continue

        title_col = col_map.get("title")
        company_col = col_map.get("company")
        for i, r in enumerate(rows[1:], start=2):
            rd = _row_dict(r, col_map)
            profile_url = (rd.get("recruiter_profile_url") or "").strip()
            if not profile_url:
                continue
            job_url = normalize_job_url(rd.get("job_url", ""))
            if not job_url:
                warnings.append(f"Tab [{tab_title}] row {i}: empty job_url; skipped.")
                continue
            jd = jd_index.get(job_url)
            if jd is None and jd_index:
                warnings.append(
                    f"Tab [{tab_title}] row {i}: job_url not in {rel_name}; skipped."
                )
                continue
            if jd is None:
                warnings.append(f"Tab [{tab_title}] row {i}: no JD available; skipped.")
                continue

            recruiter_name = rd.get("recruiter_name", "")
            job_title = rd.get("title", "") if title_col is not None else ""
            company = rd.get("company", "") if company_col is not None else ""

            if dry_run:
                items.append(
                    {
                        "profile_url": profile_url,
                        "message_text": f"[dry-run] Would generate for {recruiter_name!r}",
                    }
                )
                continue

            try:
                msg = generate_personalized_note(
                    recruiter_name=recruiter_name,
                    job_description=jd,
                    job_title=job_title,
                    company=company,
                    max_chars=max_message_chars,
                    model_name=model,
                )
            except Exception as exc:
                err = f"Tab [{tab_title}] row {i}: Gemini failed: {exc}"
                logger.exception("%s", err)
                errors.append(err)
                continue

            items.append({"profile_url": profile_url, "message_text": msg})

    return GenerateOutreachResult(
        run_date=run_date,
        items=items,
        warnings=warnings,
        errors=errors,
    )
