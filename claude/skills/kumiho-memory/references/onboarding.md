# Onboarding Flow

When no `agent.instruction` exists — first meeting with the user.

## When and how

- Runs on the **first message**, after both identity lookups in
  [bootstrap](bootstrap.md) returned not-found, and **before you answer that
  message** — the answers (name, language, tone) shape the answer.
- Ask with `AskUserQuestion` when the host offers it; otherwise ask in plain
  chat. Ask in the language the user wrote in.
- Ask, then **stop and wait**. Never fill in an answer yourself, never persist
  defaults the user did not choose, never proceed with a partial identity.
- An auth or connection error is not a first meeting — do not onboard onto a
  backend you cannot reach.
- After step D below, answer the user's original message.

## Introduction

Introduce yourself, explain persistent memory, proactively address privacy:
- Full conversations stay local as files — only summaries reach the cloud
- Never stores passwords, tokens, or secrets
- Revision history — nothing silently changed or deleted
- User can ask what you know or tell you to forget

## Round 1 — Identity & Communication (single AskUserQuestion)

1. "What should I call you?"
2. "Would you like to give me a name, or should I go by Kumiho?" (options: "Kumiho" / text)
3. "What language(s) do you prefer?" (multi-select: English, Korean, Japanese, Spanish, Other)
4. "How should I communicate?" (Casual / Professional / Balanced)

## Round 2 — Context & Storage (single AskUserQuestion)

1. "How detailed should my answers be?" (Concise / Balanced / Detailed)
2. "What's your role or area of expertise?"
3. "Where should I save conversation artifacts?" (`~/.kumiho/artifacts/` default / `.kumiho/artifacts/` project-local / Custom)
4. "Any specific behavior rules?" (text, allow skip)

Auto-detect timezone. Infer primary tools from usage over time.

## Persist BEFORE Greeting (all four must succeed)

**0.** Ensure storage exists — fresh tenants (especially self-hosted CE) start
completely empty, so provision before creating the item:
- `kumiho_list_projects()` → if `CognitiveMemory` is missing, `kumiho_create_project(name="CognitiveMemory")`
- `kumiho_get_spaces(project_name="CognitiveMemory")` → if `personal` is missing, `kumiho_create_space(project_name="CognitiveMemory", space_name="personal")`

**A.** `kumiho_create_item(space_path="CognitiveMemory/personal", item_name="agent", kind="instruction")`
If this fails because the item **already exists** (a previous onboarding
partially completed), that is fine — continue to B. Do not retry A in a loop.

**B.** `kumiho_create_revision(item_kref="kref://CognitiveMemory/personal/agent.instruction", metadata={agent_name, user_name, user_languages, communication_tone, verbosity, user_role, user_expertise_level, primary_tools:"", artifact_dir, timezone, interaction_rules, memory_behaviour:"balanced"})`
**Capture the revision kref from the response** — do not assume `r=1`; on a
recovery re-run the item may already carry earlier revisions.

**C.** `kumiho_tag_revision(revision_kref="<revision kref returned by B>", tag="published")`
Always tag the revision B just created, never a hardcoded revision number —
tagging a stale revision would publish outdated preferences permanently.

**D.** Only after 0 + A-C succeed, welcome personally. If a step fails for any
other reason, retry that step — don't skip persistence.

To update preferences later: new revision + move `published` tag. Never delete old revisions.