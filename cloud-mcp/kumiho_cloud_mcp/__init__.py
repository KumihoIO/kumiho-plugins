"""Kumiho hosted MCP resource server (``mcp.kumiho.cloud``).

Work Package C of the hosted Claude connector: an OAuth 2.1 *resource server*
that runs the Kumiho MCP tool surface in-process over streamable HTTP, one
tenant-scoped client per request. The *authorization* server lives in the
control plane (``control.kumiho.cloud``); this service only verifies the
tokens it mints.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
