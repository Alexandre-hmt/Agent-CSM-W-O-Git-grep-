"""Tool handlers — the code that runs when the LLM calls a tool.

Ported from agent.py (Sandra Triage 2.0). v4 changes versus v3:
1. search_github_code no longer uses `git grep` on a local clone — no
   shell access on this Hermes Cloud instance, and keeping a full clone
   of the company monorepo sitting on a third-party cloud instance was
   judged an unnecessary risk. It now calls the GitHub Code Search API
   instead (same mechanism as the API-method fallback, see
   `../../API METHOD/Agent.md`).
2. httpx/psycopg2 imports are defensive (see below) so the whole plugin
   still registers even if a python_dependency failed to install —
   Hermes validates `python_dependencies` in plugin.yaml but never
   installs them.

Every handler follows the Hermes plugin contract: takes (args: dict,
**kwargs), never raises, always returns a JSON string.
"""

import base64
import json
import os
import re
import threading

try:
    import httpx
except ImportError:
    class _MissingHTTPX:
        """Keep registration alive when an optional HTTP client is absent."""

        @staticmethod
        def get(*args, **kwargs):
            raise RuntimeError("HTTP tools unavailable: httpx is not installed on the Hermes backend")

        @staticmethod
        def post(*args, **kwargs):
            raise RuntimeError("HTTP tools unavailable: httpx is not installed on the Hermes backend")

    httpx = _MissingHTTPX()

# psycopg2-binary can't be installed into the sealed venv (site-packages is
# read-only on this Hermes Cloud instance). It CAN be installed to a durable,
# writable target dir (HERMES_LAZY_INSTALL_TARGET, e.g. /opt/data/lazy_installs
# — confirmed working 2026-09-11 via lazy_deps.install_specs() from an
# execute_code kernel). That install is on shared persistent storage, but this
# plugin runs in the gateway process, a different process than that kernel —
# so it must add the target dir to sys.path itself; nothing does that for it
# automatically just because the env var is set.
import sys

_lazy_install_target = os.environ.get("HERMES_LAZY_INSTALL_TARGET")
if _lazy_install_target and _lazy_install_target not in sys.path:
    sys.path.append(_lazy_install_target)

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    # Either HERMES_LAZY_INSTALL_TARGET isn't set/passed through to this
    # process, or psycopg2-binary was never installed there yet. Keep the
    # plugin loadable so non-database tools remain available regardless.
    psycopg2 = None


def _env(name, default=""):
    return os.environ.get(name, default)


def _ok(data):
    return json.dumps(data, ensure_ascii=False, default=str)


def _err(message):
    return json.dumps({"error": message}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# vapi_app / platform_app (read-only API)
# ---------------------------------------------------------------------------

_VAPI_APP_RESOURCE_PATHS = {
    "capabilities": "/capabilities/client-capabilities",
    "transfer_config": "/transfer-destinations",
    "opening_hours": "/client-opening-hours",
    "appointment_time_policy": "/appointment-time-policies",
}


def query_vapi_app(args: dict, **kwargs) -> str:
    vapi_app_url = _env("VAPI_APP_URL")
    if not vapi_app_url:
        return _err("VAPI_APP_URL not configured")

    resource = args.get("resource")
    path = _VAPI_APP_RESOURCE_PATHS.get(resource)
    if path is None:
        return _err(f"unknown resource: {resource}")

    params = {}
    for key in ("phone_number", "dealership_id", "group_id", "workshop_uuid"):
        if args.get(key):
            params[key] = args[key]

    try:
        r = httpx.get(
            f"{vapi_app_url}{path}",
            headers={"x-api-key": _env("VAPI_APP_API_KEY")},
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        return _ok({"resource": resource, "data": r.json()})
    except Exception as e:
        return _err(str(e))


def query_business_unit_schedule(args: dict, **kwargs) -> str:
    vapi_app_url = _env("VAPI_APP_URL")
    if not vapi_app_url:
        return _err("VAPI_APP_URL not configured")
    business_unit_id = args.get("business_unit_id")
    try:
        r = httpx.get(
            f"{vapi_app_url}/business-units/{business_unit_id}",
            headers={"x-api-key": _env("VAPI_APP_API_KEY")},
            timeout=15,
        )
        r.raise_for_status()
        return _ok({"business_unit_id": business_unit_id, "data": r.json()})
    except Exception as e:
        return _err(str(e))


def query_routing_destinations(args: dict, **kwargs) -> str:
    platform_app_url = _env("PLATFORM_APP_URL")
    if not platform_app_url:
        return _err("PLATFORM_APP_URL not configured")

    phone_number = args.get("phone_number")
    dealership_id = args.get("dealership_id")
    if not phone_number and not dealership_id:
        return _err("provide phone_number or dealership_id (group_id unsupported by this route)")

    path = (
        f"/routing-destinations/phone/{phone_number}"
        if phone_number
        else f"/routing-destinations/dealership/{dealership_id}"
    )
    try:
        r = httpx.get(
            f"{platform_app_url}{path}",
            headers={"x-api-key": _env("PLATFORM_APP_API_KEY")},
            timeout=15,
        )
        r.raise_for_status()
        return _ok({"data": r.json()})
    except Exception as e:
        return _err(str(e))


# ---------------------------------------------------------------------------
# Intercom
# ---------------------------------------------------------------------------


def get_intercom_ticket(args: dict, **kwargs) -> str:
    ticket_id = args.get("ticket_id")
    headers = {"Authorization": f"Bearer {_env('INTERCOM_TOKEN')}", "Accept": "application/json"}
    try:
        r = httpx.get(f"https://api.intercom.io/conversations/{ticket_id}", headers=headers)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return _err(str(e))

    source = data.get("source", {})
    source_author = source.get("author", {})

    all_messages = [
        {
            "created_at": data.get("created_at"),
            "author": source_author.get("name"),
            "author_type": source_author.get("type"),
            "body": source.get("body", ""),
            "attachments": [a.get("url") for a in source.get("attachments", [])],
        }
    ]
    hidden_reply_parts = []
    for part in data.get("conversation_parts", {}).get("conversation_parts", []):
        body = part.get("body")
        if not body:
            meta = part.get("email_message_metadata")
            if meta:
                hidden_reply_parts.append(
                    {
                        "part_id": part.get("id"),
                        "part_type": part.get("part_type"),
                        "subject": meta.get("subject"),
                        "headers": meta.get("email_address_headers"),
                    }
                )
            continue
        # Excludes internal notes — never read a human's existing
        # diagnosis and copy it instead of investigating.
        if part.get("part_type") == "note":
            continue
        author = part.get("author", {})
        all_messages.append(
            {
                "created_at": part.get("created_at"),
                "author": author.get("name"),
                "author_type": author.get("type"),
                "body": body,
                "attachments": [a.get("url") for a in part.get("attachments", [])],
            }
        )

    customer_messages = [m for m in all_messages if m["author_type"] == "user"]
    total_text_len = sum(len(re.sub(r"<[^>]+>", " ", m["body"] or "")) for m in all_messages)
    total_attachments = sum(len(m["attachments"]) for m in all_messages)

    warning = None
    if hidden_reply_parts:
        warning = (
            f"{len(hidden_reply_parts)} message(s) with email metadata (subject/sender) "
            "but NO text exposed by this API — likely a real message whose content is "
            "invisible here (e.g. a quoted/forwarded reply). Details: "
            f"{hidden_reply_parts}. Never conclude the ticket is 'empty'/'textless' on "
            "this basis alone — say so explicitly in the conclusion and recommend a "
            "manual check in the Intercom UI rather than investigating only from "
            "attachments."
        )
    elif total_text_len < 40 and total_attachments > 0:
        warning = (
            f"Near-empty total text ({total_text_len} characters) despite "
            f"{total_attachments} attachment(s) — possible missing/hidden content. "
            "Don't build the whole investigation on attachments alone without flagging "
            "this lack of textual context in the conclusion."
        )

    result = {
        "ticket_id": ticket_id,
        "title": data.get("title"),
        "email": source_author.get("email"),
        "company": data.get("company"),
        "latest_customer_message": customer_messages[-1] if customer_messages else None,
        "full_conversation": all_messages,
    }
    if warning:
        result["warning"] = warning
    return _ok(result)


def view_attachment(args: dict, **kwargs) -> str:
    url = args.get("url")
    try:
        r = httpx.get(url, timeout=15)
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "image/png").split(";")[0]
        return _ok(
            {
                "url": url,
                "media_type": content_type,
                "data_base64": base64.b64encode(r.content).decode("utf-8"),
            }
        )
    except Exception as e:
        return _err(str(e))


# ---------------------------------------------------------------------------
# Analytics DB (public schema: calls)
# ---------------------------------------------------------------------------


def _db_analytics_connect():
    if psycopg2 is None:
        raise RuntimeError(
            "Database tools unavailable: psycopg2-binary is not installed "
            "on the Hermes backend. Other Sandra tools remain available."
        )
    return psycopg2.connect(
        host=_env("DB_ANALYTICS_HOST"),
        port=_env("DB_ANALYTICS_PORT", "5432"),
        dbname=_env("DB_ANALYTICS_NAME"),
        user=_env("DB_ANALYTICS_USER"),
        password=_env("DB_ANALYTICS_PASSWORD"),
    )


# Deliberately excludes transcript/prompt/conversation_history/
# transfers_data/email_info/stereo_recording_url (thousands of tokens
# each) — available via get_call_transcript.
CALLS_ANALYTICS_SAFE_COLUMNS = """
    call_uuid, twilio_call_sid, vapi_call_uuid, call_start_datetime, call_end_datetime,
    duration, call_direction, customer_phone, assistant_phone, ended_reason,
    rdv_announced_confirmed, rdv_tool_called, rdv_datetime, first_sentence,
    "group", dealership_name, use_case, concerned_business_unit, call_type,
    call_subtype, summary, short_summary, customer_rating, customer_feedback,
    client_name, crm_client_name, vehicle_reg_num, vehicle_mileage, vehicle_make,
    vehicle_model, vehicle_year, vin, transfer_destination_phone_number,
    transfer_destination_judge, handled_by_ai, handled_by_human, urgency,
    dealership_id, group_id, ticket_id, destination_id,
    appointment_is_booked_in_software, is_appointment_booking_eligible,
    is_appointment_modification_eligible, is_appointment_cancelation_eligible,
    is_appointment_status_eligible, is_appointment_info_eligible,
    is_transfer_eligible, is_handle_by_ai_eligible, appointment_info_tool_called,
    appointment_cancelation_tool_called, appointment_modification_tool_called,
    appointment_is_canceled_in_software, appointment_is_modified_in_software,
    appointment_booking_abandon_reason, appointment_modification_abandon_reason,
    appointment_cancelation_abandon_reason, booking_failed_at_end_of_call,
    callback_promise_made, created_at
"""


def query_calls_analytics(args: dict, **kwargs) -> str:
    filters = []
    params = []

    customer_phone = args.get("customer_phone")
    if customer_phone:
        if isinstance(customer_phone, str):
            filters.append("customer_phone = %s")
            params.append(customer_phone)
        else:
            filters.append("customer_phone = ANY(%s)")
            params.append(list(customer_phone))
    if args.get("vehicle_reg_num"):
        filters.append("vehicle_reg_num ILIKE %s")
        params.append(args["vehicle_reg_num"])
    if args.get("dealership_name"):
        filters.append("dealership_name ILIKE %s")
        params.append(f"%{args['dealership_name']}%")
    if args.get("group_name"):
        filters.append('"group" ILIKE %s')
        params.append(f"%{args['group_name']}%")
    if args.get("assistant_phone"):
        filters.append("assistant_phone = %s")
        params.append(args["assistant_phone"])
    if args.get("ticket_id"):
        filters.append("ticket_id = %s")
        params.append(args["ticket_id"])
    if args.get("call_uuid"):
        filters.append("call_uuid = %s")
        params.append(args["call_uuid"])
    if args.get("vapi_call_uuid"):
        filters.append("vapi_call_uuid = %s")
        params.append(args["vapi_call_uuid"])
    if args.get("call_subtype"):
        filters.append("call_subtype = %s")
        params.append(args["call_subtype"])
    if args.get("is_appointment_booking_eligible") is not None:
        filters.append("is_appointment_booking_eligible = %s")
        params.append(args["is_appointment_booking_eligible"])
    if args.get("appointment_is_booked_in_software") is not None:
        filters.append("appointment_is_booked_in_software = %s")
        params.append(args["appointment_is_booked_in_software"])
    if args.get("booking_failed_at_end_of_call") is not None:
        filters.append("booking_failed_at_end_of_call = %s")
        params.append(args["booking_failed_at_end_of_call"])
    if args.get("call_start_date_from"):
        filters.append("call_start_datetime >= %s")
        params.append(args["call_start_date_from"])
    if args.get("call_start_date_to"):
        filters.append("call_start_datetime < (%s::date + interval '1 day')")
        params.append(args["call_start_date_to"])

    if not filters:
        return _ok([{"error": "at least one search criterion is required"}])

    try:
        conn = _db_analytics_connect()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                f"SELECT {CALLS_ANALYTICS_SAFE_COLUMNS} FROM calls WHERE {' AND '.join(filters)} "
                f"ORDER BY call_start_datetime DESC LIMIT 20;",
                params,
            )
            return _ok([dict(row) for row in cur.fetchall()])
        finally:
            conn.close()
    except Exception as e:
        return _err(str(e))


def get_call_transcript(args: dict, **kwargs) -> str:
    call_uuid = args.get("call_uuid")
    try:
        conn = _db_analytics_connect()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "SELECT call_uuid, transcript, conversation_history, first_sentence "
                "FROM calls WHERE call_uuid = %s;",
                (call_uuid,),
            )
            row = cur.fetchone()
            return _ok(dict(row) if row else {"error": f"call_uuid {call_uuid} not found"})
        finally:
            conn.close()
    except Exception as e:
        return _err(str(e))


# ---------------------------------------------------------------------------
# Analytics DB (pipecat_legacy schema: OpenTelemetry spans)
# ---------------------------------------------------------------------------


def get_pipecat_trace(args: dict, **kwargs) -> str:
    call_uuid = args.get("call_uuid")
    try:
        conn = _db_analytics_connect()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "SELECT conversation_id, trace_id, started_at, finished_at "
                "FROM pipecat_legacy.pipecat_otel_conversations WHERE call_id = %s;",
                (call_uuid,),
            )
            conv = cur.fetchone()
            if conv is None:
                return _ok(
                    {
                        "error": (
                            f"No pipecat_legacy trace for call_uuid {call_uuid} — likely "
                            "still a VAPI call, or not yet synced."
                        )
                    }
                )
            conversation_id = conv["conversation_id"]

            cur.execute(
                "SELECT turn_number, duration_seconds, was_interrupted, user_bot_latency_seconds "
                "FROM pipecat_legacy.pipecat_otel_turns WHERE conversation_id = %s ORDER BY turn_number;",
                (conversation_id,),
            )
            turns = [dict(r) for r in cur.fetchall()]

            cur.execute(
                "SELECT turn_number, stt_finalize_ms, llm_dispatch_ms, llm_ttfb_ms, tts_ttfb_ms, "
                "total_user_to_bot_ms, tool_call_count, signal_component, signal_reason "
                "FROM pipecat_legacy.pipecat_otel_turn_latencies WHERE conversation_id = %s "
                "ORDER BY turn_number;",
                (conversation_id,),
            )
            turn_latencies = [dict(r) for r in cur.fetchall()]

            cur.execute(
                "SELECT function_name, tool_type, arguments, success, result_value, "
                "duration_total_ms, http_status_code, http_error_reason "
                "FROM pipecat_legacy.pipecat_otel_tool_call_spans WHERE conversation_id = %s "
                "ORDER BY started_at;",
                (conversation_id,),
            )
            tool_call_spans = [dict(r) for r in cur.fetchall()]

            cur.execute(
                "SELECT transfer_type, match_status, destination_name, outcome_status, "
                "outcome_error_message, total_duration_ms, twilio_api_ms, dial_to_pickup_ms, "
                "pickup_to_bridge_ms, expert_pickup_result "
                "FROM pipecat_legacy.pipecat_otel_transfer_spans WHERE conversation_id = %s "
                "ORDER BY started_at;",
                (conversation_id,),
            )
            transfer_spans = [dict(r) for r in cur.fetchall()]

            return _ok(
                {
                    "call_uuid": call_uuid,
                    "conversation_id": conversation_id,
                    "trace_id": conv["trace_id"],
                    "turns": turns,
                    "turn_latencies": turn_latencies,
                    "tool_call_spans": tool_call_spans,
                    "transfer_spans": transfer_spans,
                }
            )
        finally:
            conn.close()
    except Exception as e:
        return _err(str(e))


# ---------------------------------------------------------------------------
# Source code search — GitHub Code Search API
#
# v3 used `git grep` on a local clone of the monorepo (REPO_ROOT, kept
# synced with periodic `git pull`). Dropped entirely in v4: no shell
# access on this Hermes Cloud instance to manage a clone by hand, and
# keeping the full company monorepo sitting as files on a third-party
# cloud instance was judged an unnecessary risk. This calls GitHub's
# hosted Code Search API instead — no local files, no git process.
# ---------------------------------------------------------------------------

GITHUB_REPO = "Skabadis/Sandra-AI-pipecat"
GITHUB_CODE_SEARCH_URL = "https://api.github.com/search/code"


def _resolve_path_qualifier(repo_folder: str) -> str:
    # Real bug seen with the old git-grep version (2026-09-07, ticket
    # 215475789250815): the model sometimes slips literal quotes into the
    # value (repo='"platform_app"'). Defensive cleanup carried over here.
    repo_folder = repo_folder.strip().strip("'\"")
    if repo_folder == "platform":
        return "path:platform"  # frontend — lives at repo ROOT, not under apps/
    return f"path:apps/{repo_folder}"


# Hard-coded safety net against thrashing — a prompt-only rule (the
# efficient-code-search skill) was already observed ignored for ~50 calls
# in a row on one real ticket. Keyed by ticket_id so a fresh ticket always
# starts at zero, even though this plugin stays loaded across every
# ticket the gateway ever processes.
_SEARCH_BUDGET_WARNINGS = {
    15: (
        "BUDGET: 15 calls to search_github_code on this ticket without a "
        "conclusion. Stop this tactic now: have you tried repo='platform' "
        "(the frontend, often forgotten)? Have you changed search term "
        "(exact terms already seen, not guessed names)? Otherwise, pivot "
        "to another tool (query_calls_analytics, get_pipecat_trace)."
    ),
    25: (
        "CRITICAL BUDGET: 25 calls to search_github_code on this ticket, "
        "still no conclusion. Do not call this tool again. Conclude now "
        "with what you have — 'not found' is a valid conclusion, better "
        "than hitting an iteration limit with nothing produced."
    ),
}
_search_call_counts = {}
_search_counts_lock = threading.Lock()


def search_github_code(args: dict, **kwargs) -> str:
    ticket_id = args.get("ticket_id")
    query = args.get("query")
    repo_folder = args.get("repo")
    max_results = args.get("max_results", 10)

    github_token = _env("GITHUB_SEARCH_TOKEN")
    if not github_token:
        return _err(
            "GITHUB_SEARCH_TOKEN not configured (note: NOT the same as the "
            "built-in GITHUB_TOKEN key reserved for Hermes's own Skills Hub "
            "feature — this must be a separate Custom Key)."
        )

    q = query
    if repo_folder:
        q += f" repo:{GITHUB_REPO} {_resolve_path_qualifier(repo_folder)}"
    else:
        q += f" repo:{GITHUB_REPO}"

    try:
        r = httpx.get(
            GITHUB_CODE_SEARCH_URL,
            headers={"Authorization": f"Bearer {github_token}", "Accept": "application/vnd.github+json"},
            params={"q": q, "per_page": max_results},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return _err(str(e))

    results = [
        {"path": item["path"], "html_url": item["html_url"]}
        for item in data.get("items", [])
    ]
    response = {
        "query": query,
        "repo": repo_folder,
        "total_matches": data.get("total_count", len(results)),
        "results": results,
    }

    with _search_counts_lock:
        _search_call_counts[ticket_id] = _search_call_counts.get(ticket_id, 0) + 1
        count = _search_call_counts[ticket_id]
    warning = _SEARCH_BUDGET_WARNINGS.get(count)
    if warning:
        response["_search_budget_warning"] = warning

    return _ok(response)


def get_github_file_content(args: dict, **kwargs) -> str:
    # search_github_code only gives file paths — this reads the actual
    # content of one file. Real gap found 2026-09-11 (ticket
    # 215475895231206): an unauthenticated raw.githubusercontent.com fetch
    # 404s on this private repo. The Contents API below works because it's
    # authenticated with the same GITHUB_SEARCH_TOKEN.
    path = args.get("path")
    repo = args.get("repo", GITHUB_REPO)
    ref = args.get("ref")  # optional — omit to use the repo's default branch

    github_token = _env("GITHUB_SEARCH_TOKEN")
    if not github_token:
        return _err("GITHUB_SEARCH_TOKEN not configured")
    if not path:
        return _err("path is required")

    try:
        r = httpx.get(
            f"https://api.github.com/repos/{repo}/contents/{path}",
            headers={"Authorization": f"Bearer {github_token}", "Accept": "application/vnd.github+json"},
            params={"ref": ref} if ref else {},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return _err(str(e))

    if isinstance(data, list):
        return _err(f"'{path}' is a directory, not a file — pass a file path")

    if data.get("encoding") == "base64" and data.get("content"):
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    else:
        content = data.get("content", "")

    return _ok({"path": path, "repo": repo, "sha": data.get("sha"), "size": data.get("size"), "content": content})


# ---------------------------------------------------------------------------
# Sentry — issue search + issue detail (read-only Auth Token, no OAuth)
#
# The native Sentry MCP is blocked by a platform-level OAuth bug on this
# Hermes Cloud instance (see CONTEXTE AGENT / the mcp-integrations notes
# for the full diagnosis). This calls Sentry's plain REST API instead,
# with a static Auth Token scoped to org:read/project:read/event:read —
# read-only by construction, no write scope exists to accidentally use.
# ---------------------------------------------------------------------------

SENTRY_API = "https://sentry.io/api/0"
SENTRY_ORG = "sandra-ai"


def query_sentry_issues(args: dict, **kwargs) -> str:
    query = args.get("query", "is:unresolved")
    stats_period = args.get("stats_period", "14d")
    sort = args.get("sort", "date")
    limit = args.get("limit", 25)

    sentry_token = _env("SENTRY_AUTH_TOKEN")
    if not sentry_token:
        return _err("SENTRY_AUTH_TOKEN not configured")

    try:
        r = httpx.get(
            f"{SENTRY_API}/organizations/{SENTRY_ORG}/issues/",
            headers={"Authorization": f"Bearer {sentry_token}"},
            params={"query": query, "statsPeriod": stats_period, "sort": sort, "limit": limit, "project": -1},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return _err(str(e))

    return _ok(
        [
            {
                "id": i["id"],
                "title": i["title"],
                "culprit": i.get("culprit"),
                "level": i.get("level"),
                "count": i.get("count"),
                "userCount": i.get("userCount"),
                "firstSeen": i.get("firstSeen"),
                "lastSeen": i.get("lastSeen"),
                "permalink": i.get("permalink"),
            }
            for i in data
        ]
    )


def get_sentry_issue_detail(args: dict, **kwargs) -> str:
    issue_id = args.get("issue_id")
    sentry_token = _env("SENTRY_AUTH_TOKEN")
    if not sentry_token:
        return _err("SENTRY_AUTH_TOKEN not configured")
    if not issue_id:
        return _err("issue_id is required")

    headers = {"Authorization": f"Bearer {sentry_token}"}
    try:
        r1 = httpx.get(f"{SENTRY_API}/issues/{issue_id}/", headers=headers, timeout=20)
        r1.raise_for_status()
        r2 = httpx.get(f"{SENTRY_API}/issues/{issue_id}/events/latest/", headers=headers, timeout=20)
        r2.raise_for_status()
        issue, event = r1.json(), r2.json()
    except Exception as e:
        return _err(str(e))

    return _ok(
        {
            "id": issue_id,
            "title": issue.get("title"),
            "culprit": issue.get("culprit"),
            "count": issue.get("count"),
            "permalink": issue.get("permalink"),
            "latest_event": {
                "message": event.get("message"),
                "tags": event.get("tags"),
                "exception": event.get("entries", [{}])[0].get("data") if event.get("entries") else None,
            },
        }
    )


# ---------------------------------------------------------------------------
# Writes (gated)
# ---------------------------------------------------------------------------


def post_intercom_note(args: dict, **kwargs) -> str:
    if _env("ALLOW_INTERCOM_NOTE_POSTING", "false").lower() != "true":
        return _err("Intercom note posting disabled (ALLOW_INTERCOM_NOTE_POSTING=false).")

    ticket_id = args.get("ticket_id")
    headers = {
        "Authorization": f"Bearer {_env('INTERCOM_TOKEN')}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {
        # Hard-coded — never "comment"/"reply", never visible to the customer.
        "message_type": "note",
        "type": "admin",
        "admin_id": _env("INTERCOM_ADMIN_ID"),
        "body": args.get("note"),
    }
    assert payload["message_type"] == "note"

    try:
        r = httpx.post(
            f"https://api.intercom.io/conversations/{ticket_id}/reply", headers=headers, json=payload
        )
        r.raise_for_status()
        return _ok({"status": "posted", "ticket_id": ticket_id})
    except Exception as e:
        return _err(str(e))
