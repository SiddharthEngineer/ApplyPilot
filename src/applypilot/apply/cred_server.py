"""MCP Credential Server — handles ATS login without exposing passwords to the LLM.

This standalone stdio-based MCP server reads passwords from environment variables
and uses Playwright CDP to fill login forms. The LLM calls ``ats_login`` by name
with just the ATS identifier — it never sees the password value.

Env vars expected:
    APPLYPILOT_PW_WORKDAY      — Workday password
    APPLYPILOT_PW_GREENHOUSE   — Greenhouse password
    APPLYPILOT_PW_LEVER        — Lever password
    APPLYPILOT_PW_ASHBY        — Ashby password
    CAPSOLVER_API_KEY          — CapSolver key (reserved for captcha_solve tool)

Protocol: MCP over stdio (JSON-RPC 2.0).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "ats_login",
        "description": (
            "Log in to an ATS platform using stored credentials. "
            "Connects to the same Chrome instance via CDP, finds the login "
            "form, fills email and password, and clicks Sign In."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ats": {
                    "type": "string",
                    "description": "ATS platform identifier",
                    "enum": ["workday", "greenhouse", "lever", "ashby"],
                },
                "email": {
                    "type": "string",
                    "description": "The user's email address for login",
                },
                "cdp_port": {
                    "type": "integer",
                    "description": "Chrome CDP port (default 9222)",
                    "default": 9222,
                },
            },
            "required": ["ats", "email"],
        },
    },
]

# ---------------------------------------------------------------------------
# Env var mapping
# ---------------------------------------------------------------------------

ATS_PW_ENV: dict[str, str] = {
    "workday": "APPLYPILOT_PW_WORKDAY",
    "greenhouse": "APPLYPILOT_PW_GREENHOUSE",
    "lever": "APPLYPILOT_PW_LEVER",
    "ashby": "APPLYPILOT_PW_ASHBY",
}


def _get_password(ats: str) -> str | None:
    """Read password from env var. Returns None if not set."""
    env_var = ATS_PW_ENV.get(ats)
    if not env_var:
        return None
    return os.environ.get(env_var) or None


# ---------------------------------------------------------------------------
# CDP form filling
# ---------------------------------------------------------------------------

async def _fill_login_form(ats: str, email: str, cdp_port: int) -> dict:
    """Connect to Chrome via CDP and fill the login form.

    Returns:
        {"success": bool, "message": str}
    """
    password = _get_password(ats)
    if password is None:
        return {"success": False, "message": "no_password_configured"}

    try:
        from playwright.async_api import Error as PwError
        from playwright.async_api import async_playwright
    except ImportError:
        return {"success": False, "message": "playwright_not_installed"}

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(
                f"http://localhost:{cdp_port}"
            )
    except (PwError, OSError, TimeoutError):
        return {"success": False, "message": "cdp_connection_failed"}

    try:
        # Use the first context's current page (or last page)
        contexts = browser.contexts
        if not contexts:
            return {"success": False, "message": "no_browser_context"}

        pages = contexts[0].pages
        if not pages:
            return {"success": False, "message": "no_open_pages"}

        page = pages[-1]

        # Find password field
        pw_field = await page.query_selector('input[type="password"]')
        if not pw_field:
            return {"success": False, "message": "form_not_found"}

        # Find email field (try several selectors)
        email_field = None
        for selector in [
            'input[type="email"]',
            'input[name*="email" i]',
            'input[name*="user" i]',
            'input[id*="email" i]',
            'input[id*="user" i]',
            'input[placeholder*="email" i]',
        ]:
            email_field = await page.query_selector(selector)
            if email_field:
                break

        if email_field:
            await email_field.click()
            await email_field.fill("")
            await email_field.type(email, delay=10)

        # Fill password
        await pw_field.click()
        await pw_field.fill("")
        await pw_field.type(password, delay=10)

        # Click submit button
        submit = None
        for selector in [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Sign In")',
            'button:has-text("Log In")',
            'button:has-text("Login")',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
            'button:has-text("Continue")',
            'button:has-text("Next")',
        ]:
            submit = await page.query_selector(selector)
            if submit:
                break

        if submit:
            await submit.click()
        else:
            # Fallback: press Enter on password field
            await pw_field.press("Enter")

        # Brief wait for navigation/response
        await page.wait_for_timeout(2000)

        return {"success": True, "message": "login_submitted"}

    except (PwError, OSError, ValueError, TimeoutError) as e:
        return {"success": False, "message": f"error: {e}"}


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

async def _handle_tool_call(name: str, arguments: dict) -> dict:
    """Dispatch a tool call and return the MCP result."""
    if name == "ats_login":
        ats = arguments.get("ats", "")
        email = arguments.get("email", "")
        cdp_port = arguments.get("cdp_port", 9222)

        if ats not in ATS_PW_ENV:
            return {
                "content": [{"type": "text", "text": json.dumps({
                    "success": False,
                    "message": f"unknown_ats: {ats}"
                })}],
            }

        result = await _fill_login_form(ats, email, cdp_port)
        return {
            "content": [{"type": "text", "text": json.dumps(result)}],
        }

    return {
        "content": [{"type": "text", "text": json.dumps({
            "success": False,
            "message": f"unknown_tool: {name}"
        })}],
        "isError": True,
    }


# ---------------------------------------------------------------------------
# MCP protocol handler (JSON-RPC 2.0 over stdio)
# ---------------------------------------------------------------------------

async def _handle_message(msg: dict) -> dict | None:
    """Process a single JSON-RPC message and return the response."""
    msg_id = msg.get("id")
    method = msg.get("method", "")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "applypilot-cred-server",
                    "version": "0.1.0",
                },
            },
        }

    if method == "notifications/initialized":
        # Client notification, no response needed
        return None

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": TOOLS},
        }

    if method == "tools/call":
        params = msg.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        result = await _handle_tool_call(tool_name, arguments)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": result,
        }

    # Unknown method
    if msg_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}",
            },
        }
    return None


async def _main():
    """Read JSON-RPC messages from stdin, process, write responses to stdout."""
    reader = asyncio.StreamReader()
    transport, _ = await asyncio.get_event_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
    )

    writer_transport, _ = await asyncio.get_event_loop().connect_write_pipe(
        asyncio.FlowProtocol, sys.stdout
    )

    while True:
        try:
            line = await reader.readline()
        except asyncio.CancelledError:
            break

        if not line:
            # stdin closed
            break

        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = await _handle_message(msg)
        if response is not None:
            data = json.dumps(response) + "\n"
            writer_transport.write(data.encode())
            await writer_transport.drain()

    transport.close()
    writer_transport.close()


def main():
    """Entry point for the cred-server MCP process."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()
