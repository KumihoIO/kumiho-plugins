/**
 * User identity bootstrap.
 *
 * On first contact with a senderId, stores the user's OpenClaw identity
 * as an `agent.instructions` profile in Kumiho so the same identity is
 * available when the user connects from any other Kumiho-enabled platform
 * (Claude Code, LangChain, etc.).
 *
 * Silent — never throws. Errors are logged and skipped.
 */

import type { KumihoClient } from "./client.js";
import type { ResolvedConfig } from "./types.js";

export interface UserIdentity {
  displayName?: string;
  platform?: string;
  timezone?: string;
  locale?: string;
  email?: string;
}

/**
 * Ensure a user identity profile exists in Kumiho for the given senderId.
 * No-ops if the profile already exists (one revision per user).
 * Called silently on first message; all errors are swallowed.
 */
export async function ensureUserIdentity(
  client: KumihoClient,
  config: ResolvedConfig,
  senderId: string,
  identity: UserIdentity,
): Promise<void> {
  // Nothing useful to store if we have no identity data at all
  if (!identity.displayName && !identity.platform) return;

  try {
    // Check for an existing profile scoped to this user
    const existing = await client.memoryRetrieve({
      query: `agent.instructions identity ${senderId}`,
      limit: 1,
      spacePaths: [`${config.project}/users`],
    });

    const hasProfile = existing.some(
      (m) => (m.metadata?.userId as string | undefined) === senderId,
    );

    if (hasProfile) return;

    // Build a human-readable identity summary
    const name = identity.displayName ?? senderId;
    const platform = identity.platform ?? "OpenClaw";
    const date = new Date().toISOString().slice(0, 10);

    const lines = [
      `## User Identity`,
      ``,
      `- **Name**: ${name}`,
      `- **Platform**: ${platform}`,
      `- **User ID**: ${senderId}`,
    ];
    if (identity.timezone) lines.push(`- **Timezone**: ${identity.timezone}`);
    if (identity.locale) lines.push(`- **Locale**: ${identity.locale}`);
    lines.push(``, `First seen via ${platform} on ${date}.`);
    lines.push(
      ``,
      `## Dual-Memory Agent Instructions`,
      ``,
      `This user's agent operates with two parallel memory layers:`,
      ``,
      `**Cognitive Memory** (automatic):`,
      `- Long-term memory is recalled before each turn and injected as \`<kumiho_memory>\` context`,
      `- After each turn, responses are buffered in Redis working memory`,
      `- Sessions auto-consolidate when they reach the threshold`,
      ``,
      `**Creative Memory** (explicit tools):`,
      `- Use \`creative_capture\` to permanently store any creative output (document, code, plan, design, analysis)`,
      `- Always pass \`sourceMemoryKref\` — the kref from the recalled memory that inspired this output`,
      `  This creates a DERIVED_FROM edge in the graph linking the artifact to its cognitive origin`,
      `- Use \`project_recall\` before starting or resuming project work to load past outputs`,
      `  The returned krefs can be passed as \`sourceMemoryKref\` for new captures`,
      ``,
      `**Creative capture workflow**:`,
      `1. Call \`project_recall\` with the project slug to load existing context and krefs`,
      `2. Do the creative work`,
      `3. Call \`creative_capture\` with the output, passing the relevant kref as \`sourceMemoryKref\``,
      `4. The graph pipeline runs in the background — the agent turn is never blocked`,
    );

    const userSlug = senderId
      .replace(/[^a-zA-Z0-9]/g, "-")
      .toLowerCase()
      .slice(0, 40);

    await client.memoryStore({
      spaceHint: `users/${userSlug}`,
      userText: `New user connected via ${platform}: ${name} (ID: ${senderId})`,
      assistantText: lines.join("\n"),
      type: "fact",
      title: `Identity Profile: ${name}`,
      summary: `${name} uses Kumiho via ${platform} (ID: ${senderId}${identity.timezone ? `, tz: ${identity.timezone}` : ""})`,
      topics: ["identity", "user-profile", "agent.instructions"],
      tags: ["agent.instructions", "user-profile", "latest"],
      metadata: {
        userId: senderId,
        displayName: identity.displayName ?? "",
        platform: identity.platform ?? "openclaw",
        timezone: identity.timezone ?? "",
        locale: identity.locale ?? "",
        firstSeen: date,
      },
    });
  } catch {
    // Identity bootstrap is best-effort — never block the message turn
  }
}
