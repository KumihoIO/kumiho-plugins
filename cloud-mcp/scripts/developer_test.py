#!/usr/bin/env python3
"""Exercise the deployed Kumiho MCP through real browser OAuth, without saving tokens.

Default: public probes only. --login adds authenticated discovery/read tests.
--writes additionally creates a unique synthetic memory and two disposable buffers.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import secrets
import sys
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MCP_URL = "https://mcp.kumiho.cloud/mcp"
ISSUER = "https://control.kumiho.cloud"
REPORT = {"mcp_url": MCP_URL, "started_at": datetime.now(timezone.utc).isoformat(), "checks": []}


def check(name: str, condition: bool) -> None:
    REPORT["checks"].append({"name": name, "passed": bool(condition)})
    print(f"{'PASS' if condition else 'FAIL'} {name}", flush=True)
    if not condition:
        raise RuntimeError(name)


def endpoint(metadata: dict, name: str) -> str:
    value = metadata.get(name, "")
    url = urlsplit(value)
    check(f"trusted {name}", url.scheme == "https" and url.netloc == "control.kumiho.cloud" and not url.fragment)
    return value


def public_probes(client: httpx.Client) -> dict:
    health = client.get("https://mcp.kumiho.cloud/healthz")
    check("origin health", health.status_code == 200 and health.json().get("status") == "ok")
    resource = client.get("https://mcp.kumiho.cloud/.well-known/oauth-protected-resource")
    check("resource discovery", resource.status_code == 200)
    prm = resource.json()
    check("resource and issuer binding", prm.get("resource") == MCP_URL and prm.get("authorization_servers") == [ISSUER])
    denied = client.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers={"accept": "application/json, text/event-stream"})
    check("unauthenticated tools rejected", denied.status_code == 401 and "resource_metadata=" in denied.headers.get("www-authenticate", ""))
    response = client.get(ISSUER + "/.well-known/oauth-authorization-server")
    check("authorization discovery", response.status_code == 200)
    metadata = response.json()
    check("issuer and PKCE S256", metadata.get("issuer") == ISSUER and "S256" in metadata.get("code_challenge_methods_supported", []))
    return metadata


def callback_valid(query: dict, state: str) -> bool:
    return (len(query.get("state", [])) == 1 and len(query.get("iss", [])) == 1
            and secrets.compare_digest(query["state"][0].encode(), state.encode()) and query["iss"][0] == ISSUER)


def login(client: httpx.Client, metadata: dict, no_browser: bool) -> tuple[dict, str]:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    received = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Callback URLs contain a single-use authorization code.

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            valid = parsed.path == "/callback" and callback_valid(query, state)
            body = b"Kumiho sign-in completed. Return to Codex or your terminal." if valid else b"Invalid OAuth callback. Return to the sign-in window."
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)
            if valid:
                received.update(query)

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 1
        redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"
        registered = client.post(endpoint(metadata, "registration_endpoint"), json={
            "client_name": "Kumiho Developer Test", "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "scope": "memory offline_access",
        })
        check("public client registration", registered.status_code == 201)
        client_id = registered.json()["client_id"]
        auth_url = endpoint(metadata, "authorization_endpoint") + "?" + urlencode({
            "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": "memory offline_access", "resource": MCP_URL, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        print("Sign in to Kumiho and approve Kumiho Developer Test in the browser.", flush=True)
        print("LOGIN_URL=" + auth_url, flush=True)  # Only public client/PKCE parameters, never tokens.
        if not no_browser:
            webbrowser.open(auth_url)
        deadline = time.monotonic() + 600
        while not received and time.monotonic() < deadline:
            server.handle_request()
        check("browser consent callback", bool(received) and "code" in received and "error" not in received)
        form = {"grant_type": "authorization_code", "client_id": client_id, "redirect_uri": redirect_uri,
                "code": received["code"][0], "code_verifier": verifier, "resource": MCP_URL}
        issued = client.post(endpoint(metadata, "token_endpoint"), data=form)
        check("authorization code exchange", issued.status_code == 200)
        token = issued.json()
        # Replaying this code revokes its refresh family. Keep destructive
        # replay probes separate from the normal refresh/tool lifecycle.
        return token, client_id


def valid_token_pair(token) -> bool:
    return (isinstance(token, dict)
            and all(isinstance(token.get(key), str) and bool(token[key].strip())
                    for key in ("access_token", "refresh_token", "token_type"))
            and token["token_type"].lower() == "bearer")


def payload(result):
    text = "\n".join(getattr(block, "text", "") or "" for block in result.content)
    try:
        return json.loads(text)
    except ValueError:
        return {"message": text}


async def authenticated_checks(access_token: str, writes: bool) -> None:
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + access_token}, timeout=120) as http:
        async with streamable_http_client(MCP_URL, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                check("MCP initialize", bool(init.server_info.name))
                catalog = (await session.list_tools()).tools
                expected = json.loads((Path(__file__).resolve().parents[1] / "chatgpt-app-submission.json").read_text(encoding="utf-8"))["tools"]
                check("18 reviewed tools", {t.name for t in catalog} == set(expected) and len(catalog) == 18)
                for tool in catalog:
                    exported = tool.model_dump(by_alias=True)
                    check(tool.name + " annotations", all(exported["annotations"].get(k) == v for k, v in expected[tool.name]["annotations"].items()))
                search = next(t for t in catalog if t.name == "kumiho_search_items")
                check("no credential input", "auth_token" not in search.input_schema.get("properties", {}))

                async def call(name, arguments=None, allow_error=False):
                    result = await session.call_tool(name, arguments or {})
                    data = payload(result)
                    if not allow_error:
                        check(name, not result.is_error and not (isinstance(data, dict) and data.get("error")))
                    return bool(result.is_error), data

                _, projects = await call("kumiho_list_projects")
                del projects  # Do not put private workspace listings in a report.
                if not writes:
                    return
                run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
                space = "developer-tests/" + run_id
                REPORT["fixture_space"] = "CognitiveMemory/" + space
                _, stored = await call("kumiho_memory_store", {
                    "project": "CognitiveMemory", "space_path": space, "stack_revisions": False,
                    "title": "Developer test " + run_id,
                    "user_text": "Synthetic developer test: the Seoul pilot uses Seoul to reduce latency for Korean users.",
                    "summary": "Synthetic test decision: Seoul was chosen to reduce latency for the Korean pilot.",
                    "memory_type": "decision", "tags": ["published", "developer-test", run_id],
                })
                kref = stored.get("revision_kref") or stored.get("item_kref") or ""
                check("test memory stayed in its new space", "/" + space + "/" in kref)
                item = kref.split("?", 1)[0]
                REPORT["fixture_kref"] = item
                _, fetched = await call("kumiho_get_item", {"kref": item})
                check("stored fixture readable", fetched.get("kref") == item)
                _, retrieved = await call("kumiho_memory_retrieve", {"project": "CognitiveMemory", "query": run_id, "space_paths": ["CognitiveMemory/" + space], "limit": 3})
                check("search returns the created fixture", item in retrieved.get("item_krefs", []))
                # Only this newly-created fixture can be retired/restored.
                await call("kumiho_deprecate_item", {"item_kref": item, "deprecated": True})
                await call("kumiho_deprecate_item", {"item_kref": item, "deprecated": False})
                ids = []
                try:
                    for _ in range(2):
                        error, issued = await call("kumiho_chat_get", allow_error=True)
                        check("server issues a conversation ID", error and issued.get("error") == "session_required" and bool(issued.get("session_id")))
                        ids.append(issued["session_id"])
                    check("two distinct conversations", ids[0] != ids[1])
                    for index, sid in enumerate(ids):
                        captures = [{
                            "type": "decision", "title": "Reflect capture " + run_id,
                            "content": "Synthetic test: keep hosted SDK handles scoped to the active caller.",
                        }] if index == 0 else []
                        _, reflected = await call("kumiho_memory_reflect", {
                            "session_id": sid, "response": f"Synthetic buffer {index} for {run_id}",
                            "space_path": space + "/reflect", "captures": captures, "discover_edges": False,
                        })
                        if captures:
                            refs = reflected.get("stored_krefs", [])
                            check("reflect stores a capture in its test space", reflected.get("captures_stored") == 1 and len(refs) == 1 and "/" + space + "/reflect/" in refs[0])
                    for index, sid in enumerate(ids):
                        _, data = await call("kumiho_chat_get", {"session_id": sid})
                        serialized = json.dumps(data)
                        check(f"conversation {index} isolated", f"Synthetic buffer {index}" in serialized and f"Synthetic buffer {1-index}" not in serialized)
                    await call("kumiho_chat_clear", {"session_id": ids[0]})
                    _, first = await call("kumiho_chat_get", {"session_id": ids[0]})
                    _, second = await call("kumiho_chat_get", {"session_id": ids[1]})
                    check("clear affects only its own conversation", not first.get("messages") and "Synthetic buffer 1" in json.dumps(second))
                finally:
                    cleanup_failed = False
                    for sid in ids:
                        try:
                            await call("kumiho_chat_clear", {"session_id": sid})
                        except Exception:
                            cleanup_failed = True
                    if cleanup_failed:
                        REPORT["buffer_cleanup"] = "Some temporary buffers need cleanup verification"
                    check("all temporary buffers cleaned", not cleanup_failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--writes", action="store_true", help="Create and retain one synthetic memory; retire/restore it; create and clear two temporary buffers")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.writes and not args.login:
        parser.error("--writes requires --login")
    token = {}
    client_id = ""
    metadata = {}
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            metadata = public_probes(client)
            if args.login:
                if args.writes:
                    print("Write tests are enabled: one new synthetic memory and two temporary buffers only.", flush=True)
                token, client_id = login(client, metadata, args.no_browser)
                # Take ownership before validation so even a partial issuance is revoked.
                check("bearer and refresh issued", valid_token_pair(token))
                refreshed = client.post(endpoint(metadata, "token_endpoint"), data={
                    "grant_type": "refresh_token", "client_id": client_id,
                    "refresh_token": token["refresh_token"], "resource": MCP_URL,
                })
                successor = refreshed.json() if refreshed.status_code == 200 else {}
                check("refresh rotation", valid_token_pair(successor) and successor["refresh_token"] != token["refresh_token"])
                # Keep the prior refresh token for family revocation if validation fails.
                token = successor
                asyncio.run(authenticated_checks(token["access_token"], args.writes))
        REPORT["status"] = "passed"
    except Exception as exc:
        # Exceptions may contain callback URLs or response data. Never print them.
        REPORT["status"] = "failed"
        REPORT["exception_type"] = type(exc).__name__
        print("Test stopped: " + type(exc).__name__ + ". Inspect the last named check; tokens were not saved.", flush=True)
    finally:
        if token.get("refresh_token") and client_id:
            try:
                with httpx.Client(timeout=30) as client:
                    revoked = client.post(endpoint(metadata, "revocation_endpoint"), data={"token": token["refresh_token"], "token_type_hint": "refresh_token", "client_id": client_id})
                    check("test refresh token revoked", revoked.status_code == 200)
                    denied = client.post(endpoint(metadata, "token_endpoint"), data={"grant_type": "refresh_token", "client_id": client_id, "refresh_token": token["refresh_token"], "resource": MCP_URL})
                    check("revoked refresh token rejected", denied.status_code == 400 and denied.json().get("error") == "invalid_grant")
            except Exception:
                REPORT["cleanup"] = "refresh revocation needs verification"
                REPORT["status"] = "failed"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(REPORT, indent=2) + "\n", encoding="utf-8")
        print("No tokens, passwords or private workspace listings were written to the report.", flush=True)
    return 0 if REPORT.get("status") == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
