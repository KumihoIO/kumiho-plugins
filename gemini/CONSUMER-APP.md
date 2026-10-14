# Kumiho Memory for Gemini web and mobile

Status on **2026-10-14**: hosted connector prepared; **not submitted or publicly
listed** in Google's Connected Apps catalog. No documented public vendor
submission form was found. A custom connection is not a catalog listing.

## Current Google eligibility

[Google's custom-app guide](https://support.google.com/gemini/answer/17209137?hl=en)
requires a personal Google Account, age 18+, US location and Keep Activity on.
The feature is English-only and unavailable to work or school accounts.
A connection added on the web can then be used on web and mobile.

The signed-in Kumiho Workspace account's Connectors screen showed supported
catalog apps but no custom-app add control. Do not change account location,
enable Activity or buy a subscription to bypass these limits.

## Connect when eligible

1. Open Gemini web's Connected Apps settings.
2. Under Custom apps add `https://mcp.kumiho.cloud/mcp`.
3. Continue through Kumiho OAuth and choose the intended workspace.
4. Use `@` to select Kumiho; try a read-only project/access check.
5. For a save/recall test, explicitly request one harmless fact, approve the
   write, and recall it in a new conversation.

Kumiho supports OAuth discovery, dynamic registration and PKCE. Users should
not need Advanced credentials. Google manages the client-side OAuth session.
Do not enter passwords, access tokens or reviewer credentials into prompts.

The endpoint already implements the hosted memory tool profile; no Gemini
API wrapper, local bridge or Google Cloud service account is needed. Refer to
the current tool schemas, not a model-generated tool catalog.

## Proposed public listing

- Name: Kumiho Memory
- Publisher: Kumiho Inc.
- Description: Remember decisions, facts and preferences in your private Kumiho
  workspace, then recall relevant context in later conversations.
- Support: support@kumiho.io
- Website: https://kumiho.io
- Privacy: https://kumiho.io/en/legal#privacy
- Terms: https://kumiho.io/en/legal#terms
- MCP: https://mcp.kumiho.cloud/mcp
- OAuth issuer: https://control.kumiho.cloud
- Requested scope: memory (read and write)
- Logo: `cli/logo.png`
- Examples: "Recall our saved project decisions and reasons."
  "Remember this approved decision."
  "Preview a review of five memories about this project."

Security and data-processing declarations must describe actual production
behavior. Scope includes reads and writes; buffer clearing is irreversible,
while retiring a memory is reversible and is not erasure. A Cloud workspace
is required. Reviewer access must use an isolated workspace and a secure
credential submission field, never this document.

## Outstanding public registration

Obtain a Google-confirmed vendor intake route for the **consumer Gemini
Connected Apps catalog**. Gemini CLI gallery, Gemini Enterprise organization
connectors, Workspace Marketplace and Gemini API partnerships are different
distribution surfaces and do not establish consumer catalog registration.

Official references:
- https://support.google.com/gemini/answer/17209137?hl=en
- https://support.google.com/gemini/answer/13695044
- https://blog.google/innovation-and-ai/products/gemini-app/gemini-spark-updates-june-2026/
- https://blog.google/innovation-and-ai/products/gemini-app/new-connected-apps-gemini/
