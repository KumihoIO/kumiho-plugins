"""kumiho-gpt-connect — expose Kumiho Memory to ChatGPT as a custom MCP connector.

Architecture (CE / free tier):

    ChatGPT  ──OAuth (auth code + PKCE, DCR)──►  Gateway (this package)
             ──MCP over HTTPS (streamable/SSE)─►      │
                                                      │ reverse-proxy (Bearer-checked)
                                                      ▼
                                                 mcp-proxy ──stdio──► kumiho-mcp ──► CE (127.0.0.1:9190)

The gateway is its own tiny OAuth 2.1 authorization server: ChatGPT registers
dynamically, the user approves once with a one-time PIN printed by the
installer, and every MCP request then carries a short-lived signed Bearer.
The token never rides in the connector URL.
"""

__version__ = "0.1.0"
