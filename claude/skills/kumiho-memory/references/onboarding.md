# Onboarding Flow

When no `agent.instruction` exists — first meeting with the user.

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

## Persist BEFORE Greeting (all three must succeed)

**A.** `kumiho_create_item(space_path="CognitiveMemory", item_name="agent", kind="instruction")`

**B.** `kumiho_create_revision(item_kref="kref://CognitiveMemory/agent.instruction", metadata={agent_name, user_name, user_languages, communication_tone, verbosity, user_role, user_expertise_level, primary_tools:"", artifact_dir, timezone, interaction_rules, memory_behaviour:"balanced"})`

**C.** `kumiho_tag_revision(revision_kref="kref://CognitiveMemory/agent.instruction?r=1", tag="published")`

**D.** Only after A-C succeed, welcome personally. If any fail, retry — don't skip persistence.

To update preferences later: new revision + move `published` tag. Never delete old revisions.