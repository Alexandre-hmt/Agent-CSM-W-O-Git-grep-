from . import schemas, tools


def register(ctx):
    """Called once at Hermes startup to wire schemas to handlers.

    toolset="project" is deliberate, not a placeholder: a custom toolset
    name (e.g. "sandra-triage-tools-v3") never gets attached to a session
    by default on this Hermes Cloud instance, so its tools never appear —
    confirmed with our own plugin and with Nous Research's own official
    example plugin. "project" is already attached to every session, so
    tools registered under it are visible without any extra step.
    """
    ctx.register_tool(
        name="get_intercom_ticket",
        toolset="project",
        schema=schemas.GET_INTERCOM_TICKET,
        handler=tools.get_intercom_ticket,
    )
    ctx.register_tool(
        name="view_attachment",
        toolset="project",
        schema=schemas.VIEW_ATTACHMENT,
        handler=tools.view_attachment,
    )
    ctx.register_tool(
        name="query_vapi_app",
        toolset="project",
        schema=schemas.QUERY_VAPI_APP,
        handler=tools.query_vapi_app,
    )
    ctx.register_tool(
        name="query_business_unit_schedule",
        toolset="project",
        schema=schemas.QUERY_BUSINESS_UNIT_SCHEDULE,
        handler=tools.query_business_unit_schedule,
    )
    ctx.register_tool(
        name="query_routing_destinations",
        toolset="project",
        schema=schemas.QUERY_ROUTING_DESTINATIONS,
        handler=tools.query_routing_destinations,
    )
    ctx.register_tool(
        name="query_calls_analytics",
        toolset="project",
        schema=schemas.QUERY_CALLS_ANALYTICS,
        handler=tools.query_calls_analytics,
    )
    ctx.register_tool(
        name="get_call_transcript",
        toolset="project",
        schema=schemas.GET_CALL_TRANSCRIPT,
        handler=tools.get_call_transcript,
    )
    ctx.register_tool(
        name="get_pipecat_trace",
        toolset="project",
        schema=schemas.GET_PIPECAT_TRACE,
        handler=tools.get_pipecat_trace,
    )
    ctx.register_tool(
        name="search_github_code",
        toolset="project",
        schema=schemas.SEARCH_GITHUB_CODE,
        handler=tools.search_github_code,
    )
    ctx.register_tool(
        name="post_intercom_note",
        toolset="project",
        schema=schemas.POST_INTERCOM_NOTE,
        handler=tools.post_intercom_note,
    )
