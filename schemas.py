"""Tool schemas — what the LLM reads to decide when/how to call each tool.

Ported from agent.py's original `TOOLS` list (Sandra Triage 2.0),
translated to English, `input_schema` renamed to `parameters` per Hermes's
plugin convention.
"""

GET_INTERCOM_TICKET = {
    "name": "get_intercom_ticket",
    "description": (
        "Fetches the latest customer message, email, and the full "
        "conversation (attachments included) for an Intercom ticket. If "
        "the result contains a \"warning\" key, the Intercom API is "
        "probably hiding a real message's text (confirmed empirically: a "
        "quoted/forwarded reply can be invisible here while visible "
        "manually in the Intercom UI). Never conclude a ticket is "
        "\"empty\"/\"textless\" on that basis alone: if present, flag it "
        "explicitly in the conclusion and recommend a manual check rather "
        "than building the whole investigation on attachments alone."
    ),
    "parameters": {
        "type": "object",
        "properties": {"ticket_id": {"type": "string"}},
        "required": ["ticket_id"],
    },
}

VIEW_ATTACHMENT = {
    "name": "view_attachment",
    "description": (
        "Downloads and displays an image attached to a ticket (screenshot, "
        "etc.) so you can actually see it. Use only when the ticket text "
        "isn't enough to understand the problem — don't call it "
        "systematically on every attachment."
    ),
    "parameters": {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
}

QUERY_VAPI_APP = {
    "name": "query_vapi_app",
    "description": (
        "Read-only, via the vapi_app API (no DB connection), the "
        "effective config for a phone number/dealership/group. "
        "resource=\"capabilities\" (enabled/disabled, e.g. "
        "warm_transfer_enabled/routing_transfer_enabled — often the first "
        "reflex at Level 2); \"transfer_config\" (business units + "
        "transfer_destinations, LIVE transfer during the call); "
        "\"opening_hours\"; \"appointment_time_policy\" (field "
        "reception_forbidden_days — this mechanism, NOT opening_hours, "
        "actually filters the appointment slots proposed to the customer. "
        "For a \"appointment proposed/booked on a closed day\" ticket, "
        "always check BOTH: opening_hours can be correct — no line for "
        "that day — while reception_forbidden_days is empty/null and "
        "therefore blocks nothing in practice). For post-call ROUTING "
        "(categorization after the call), see query_routing_destinations "
        "separately. For transfer_config/opening_hours/"
        "appointment_time_policy (not capabilities): phone_number alone "
        "often returns [] even when a config really exists — the data "
        "typically lives at the dealership_id level. If phone_number "
        "returns [], always retry with dealership_id before concluding no "
        "config exists."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "resource": {
                "type": "string",
                "enum": ["capabilities", "transfer_config", "opening_hours", "appointment_time_policy"],
            },
            "phone_number": {"type": "string"},
            "dealership_id": {"type": "string"},
            "group_id": {"type": "string"},
            "workshop_uuid": {"type": "string", "description": "only used by appointment_time_policy"},
        },
        "required": ["resource"],
    },
}

QUERY_BUSINESS_UNIT_SCHEDULE = {
    "name": "query_business_unit_schedule",
    "description": (
        "Reads a business unit and ALL its nested schedules/hours. THIS "
        "mechanism — NOT the \"hours\" field on a transfer_destination — "
        "actually determines whether a transfer destination is considered "
        "reachable at a given moment: a transfer_destination has a "
        "business_unit_id (returned by "
        "query_vapi_app(resource=\"transfer_config\")); if a schedule of "
        "that business unit matches the context (priority phone_number > "
        "dealership_id > group_id > shared), its hours ALWAYS take "
        "priority over the destination's own \"hours\" field (a last "
        "resort, never consulted if a matching schedule exists). For a "
        "\"transfer proposed/executed at a time it shouldn't have been\" "
        "ticket, check THIS mechanism, not just the hours shown on the "
        "destination."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "business_unit_id": {
                "type": "string",
                "description": "comes from a transfer_destination's business_unit_id field",
            },
        },
        "required": ["business_unit_id"],
    },
}

QUERY_ROUTING_DESTINATIONS = {
    "name": "query_routing_destinations",
    "description": (
        "Read-only, via the platform_app API, post-call ROUTING "
        "destinations (e.g. \"Sales Secretary\" vs \"Workshop\") — "
        "DIFFERENT from a live transfer (query_vapi_app "
        "resource=\"transfer_config\"), confirmed empirically: a "
        "dealership with a real recent transfer has an EMPTY "
        "transfer_config on the vapi_app side but a non-empty result "
        "here — post-call routing is often the mechanism actually used. "
        "Returns destination_info: the keywords used by the AI router to "
        "classify — an overly generic/ambiguous keyword here often "
        "explains a misrouting. phone_number OR dealership_id required "
        "(not both, not group_id — unsupported by this route with this "
        "agent's static API key)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "phone_number": {"type": "string"},
            "dealership_id": {"type": "string"},
        },
    },
}

QUERY_CALLS_ANALYTICS = {
    "name": "query_calls_analytics",
    "description": (
        "Searches calls in the analytics DB (calls table — outcome/result "
        "columns, no raw transcript) from any combination of identifying "
        "elements. At least one criterion required. Always combine an "
        "unindexed field with customer_phone or a date range. Returns "
        "assistant_phone (not the dealership/switchboard number) — pass "
        "that to query_vapi_app. If the returned fields (e.g. "
        "abandon_reason) are too vague, see get_pipecat_trace."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "customer_phone": {
                "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                "description": "One number, or a list of candidate numbers to test in a single search.",
            },
            "vehicle_reg_num": {"type": "string"},
            "dealership_name": {"type": "string"},
            "group_name": {"type": "string"},
            "assistant_phone": {"type": "string"},
            "ticket_id": {"type": "string", "description": "dashboard ticket_id (not the Intercom id)"},
            "call_uuid": {"type": "string"},
            "vapi_call_uuid": {"type": "string"},
            "call_subtype": {"type": "string"},
            "is_appointment_booking_eligible": {"type": "boolean"},
            "appointment_is_booked_in_software": {"type": "boolean"},
            "booking_failed_at_end_of_call": {"type": "boolean"},
            "call_start_date_from": {"type": "string", "description": "YYYY-MM-DD"},
            "call_start_date_to": {"type": "string", "description": "YYYY-MM-DD"},
        },
    },
}

GET_CALL_TRANSCRIPT = {
    "name": "get_call_transcript",
    "description": (
        "Fetches the full transcript and conversation history for one "
        "specific call (call_uuid) — kept separate from "
        "query_calls_analytics so this heavy content is never loaded by "
        "default. Call only if structured fields aren't enough to "
        "understand what was said."
    ),
    "parameters": {
        "type": "object",
        "properties": {"call_uuid": {"type": "string"}},
        "required": ["call_uuid"],
    },
}

GET_PIPECAT_TRACE = {
    "name": "get_pipecat_trace",
    "description": (
        "Reconstructs the precise technical chain of a call (Level 3 "
        "only — call only if query_calls_analytics alone isn't enough): "
        "turns, per-turn STT/LLM/TTS latency, tool calls with their exact "
        "HTTP status (this is what lets you distinguish a bug on our side "
        "from a third-party outage), and transfers with second-by-second "
        "detail. Only covers calls that went through the Pipecat "
        "pipeline."
    ),
    "parameters": {
        "type": "object",
        "properties": {"call_uuid": {"type": "string"}},
        "required": ["call_uuid"],
    },
}

SEARCH_GITHUB_CODE = {
    "name": "search_github_code",
    "description": (
        "Searches code in the Sandra-AI-pipecat monorepo via the GitHub "
        "Code Search API (no shell, no local clone on this instance — "
        "text/keyword search, not semantic, no regex). Rate-limited to "
        "~10 requests/minute. `repo` scopes the search to one monorepo "
        "folder — e.g. 'vapi_app' or 'platform_app' (both resolved under "
        "apps/), or 'platform' (the FRONTEND, React — a ROOT folder of "
        "the monorepo, NOT under apps/, so pass it exactly as 'platform'). "
        "Omit `repo` to search the whole monorepo (slower, last resort). "
        "Unlike the previous git-grep version, you can only scope ONE "
        "folder per call now (no OR across a list) — if several folders "
        "are plausible, call once per candidate rather than passing a "
        "list. As soon as a ticket looks like a DISPLAY issue (a "
        "status/value that differs depending on who's looking, a screen "
        "that doesn't refresh, a value visibly wrong on screen while "
        "correct in the database), search 'platform' — sometimes even "
        "before backend repos, not just as a last resort. Pass the same "
        "ticket_id on every call for one ticket so the anti-thrashing "
        "budget is tracked correctly. A search returning nothing for a "
        "term you're confident exists may be an indexing lag or a query- "
        "syntax limitation, not proof of absence."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ticket_id": {
                "type": "string",
                "description": "the Intercom ticket currently being triaged — scopes the anti-thrashing budget",
            },
            "query": {"type": "string"},
            "repo": {
                "type": "string",
                "description": (
                    "one monorepo folder, e.g. 'vapi_app', 'platform_app', "
                    "or 'platform' (frontend, root folder). Omit to search "
                    "the whole monorepo."
                ),
            },
            "max_results": {"type": "integer"},
        },
        "required": ["ticket_id", "query"],
    },
}

GET_GITHUB_FILE_CONTENT = {
    "name": "get_github_file_content",
    "description": (
        "Reads the actual content of one file in the monorepo — "
        "search_github_code only gives file paths/matches, never the "
        "content itself. Call this once search_github_code has narrowed "
        "down a specific file, to actually read the code and confirm "
        "what it does rather than guessing from the path/match snippet "
        "alone. Uses GitHub's authenticated Contents API — an "
        "unauthenticated raw.githubusercontent.com fetch will 404 on "
        "this private repo."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path within the repo, e.g. 'apps/vapi_app/src/integrators/gateway/models.py'."},
            "repo": {"type": "string", "description": "owner/repo, defaults to Skabadis/Sandra-AI-pipecat."},
            "ref": {"type": "string", "description": "branch/commit/tag — omit to use the default branch."},
        },
        "required": ["path"],
    },
}

QUERY_SENTRY_ISSUES = {
    "name": "query_sentry_issues",
    "description": (
        "Searches Sentry issues across the whole org via the plain REST "
        "API (read-only Auth Token, no OAuth — the native Sentry MCP is "
        "blocked by a platform bug on this instance). No project slug "
        "needed — it queries across all projects. Keep `query`/"
        "`stats_period` tied to something already known from the ticket "
        "(a dealership/customer/error type) — never a blanket unscoped "
        "search. If nothing relevant turns up, say so rather than "
        "broadening the search repeatedly."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Sentry search syntax, e.g. 'is:unresolved' or a free-text term. Defaults to 'is:unresolved'.",
            },
            "stats_period": {
                "type": "string",
                "description": "How far back, e.g. '24h', '14d', '90d'. Defaults to '14d'.",
            },
            "sort": {"type": "string", "description": "e.g. 'date', 'new', 'freq'. Defaults to 'date'."},
            "limit": {"type": "integer"},
        },
    },
}

GET_SENTRY_ISSUE_DETAIL = {
    "name": "get_sentry_issue_detail",
    "description": (
        "Fetches full detail for one specific Sentry issue, including the "
        "latest event's message/tags/exception data — call this after "
        "query_sentry_issues has narrowed down a specific issue id."
    ),
    "parameters": {
        "type": "object",
        "properties": {"issue_id": {"type": "string"}},
        "required": ["issue_id"],
    },
}

POST_INTERCOM_NOTE = {
    "name": "post_intercom_note",
    "description": (
        "Posts an INTERNAL note on the Intercom ticket (never visible to "
        "the customer, never a reply)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["ticket_id", "note"],
    },
}
