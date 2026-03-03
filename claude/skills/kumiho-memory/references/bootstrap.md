# Session Bootstrap Procedure

Runs ONCE on the first user message. Never repeat.

## Step 1 — Auth check & identity load

```text
kumiho_get_revision_by_tag(
  item_kref = "kref://CognitiveMemory/agent.instruction",
  tag       = "published"
)
```

| Result | Action |
| ------ | ------ |
| Revision returned | Parse metadata fields below, adopt identity → Step 2 |
| Item/tag not found | First session → [Onboarding](onboarding.md) |
| Auth error (401 / UNAUTHENTICATED) | Say: "Memory isn't connected yet — run `/kumiho-auth` to set up, then start a new session (or restart the app on Claude Desktop). If you don't have an account yet, sign up free at kumiho.io." Continue without memory. |
| Connection error (UNAVAILABLE / connection refused / DNS resolution failure) | Same message as auth error. The server started before a token was available, so discovery didn't resolve the cloud endpoint. |
| Any other error | Log the error silently and continue without memory. Do NOT show raw gRPC errors or stack traces to the user. |

## Step 2 — Identity adoption & context load

1. Parse metadata, adopt agent identity and communication style.
2. `kumiho_memory_recall` **once** with a broad query (user's name, role, recent topics). This is the ONLY recall for the first turn — do not call recall again.
3. **Greeting rule** — Only greet if the user's message is itself a greeting (e.g. "hi", "hey", "good morning").  If the user opens with a question or task, skip the greeting and answer directly.  Sessions can pause and resume — do NOT treat every session start as a first meeting.  Never say things like "Good that memory's connected!" or narrate the bootstrap.

## Identity Metadata Fields

`agent_name`, `user_name`, `user_languages`, `communication_tone` (casual/professional/balanced), `verbosity` (concise/balanced/detailed), `user_role`, `user_expertise_level`, `primary_tools`, `artifact_dir`, `timezone`, `interaction_rules`, `memory_behaviour`
