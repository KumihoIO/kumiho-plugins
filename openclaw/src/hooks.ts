/**
 * Auto-recall and auto-capture hooks for OpenClaw.
 *
 * - Auto-Recall (before_agent_start): Searches Kumiho for memories matching
 *   the current message and injects them into the agent context.
 *
 * - Auto-Capture (agent_end): After the agent responds, extracts noteworthy
 *   facts from the exchange and stores them in Kumiho Cloud.
 *
 * Both hooks respect the privacy model: only structured summaries are sent
 * to the cloud, and PII is redacted before upload.
 */

import type { KumihoClient } from "./client.js";
import type { PIIRedactor } from "./privacy.js";
import type { ArtifactManager } from "./artifacts.js";
import type { ResolvedConfig, MemoryEntry, ChannelInfo } from "./types.js";
import { generateSessionId, getMemorySpace, buildChannelMetadata } from "./session.js";

// ---------------------------------------------------------------------------
// Shared state across hooks within a single request cycle
// ---------------------------------------------------------------------------

export interface HookState {
  sessionId: string | null;
  lastUserMessage: string | null;
  lastAssistantResponse: string | null;
  messageCount: number;
  recalledMemories: MemoryEntry[];
  /** Sender IDs whose identity profile has already been bootstrapped this session. */
  identityStoredFor: Set<string>;
  /**
   * Stale-while-revalidate prefetch: memories fetched in the background during
   * agent_end (while the user reads the response). Consumed instantly on the
   * next before_prompt_build with zero added latency.
   */
  prefetchedRecall: { contextInjection: string; memories: MemoryEntry[] } | null;
}

export function createHookState(): HookState {
  return {
    sessionId: null,
    lastUserMessage: null,
    lastAssistantResponse: null,
    messageCount: 0,
    recalledMemories: [],
    identityStoredFor: new Set(),
    prefetchedRecall: null,
  };
}

/**
 * Read-only memory fetch — no side effects on HookState.
 * Used for background prefetching during agent_end so the result is
 * ready instantly on the next before_prompt_build.
 */
export async function prefetchMemories(
  client: KumihoClient,
  config: ResolvedConfig,
  query: string,
): Promise<{ contextInjection: string; memories: MemoryEntry[] }> {
  const memories = await client.memoryRetrieve({ query, limit: config.topK });
  const relevant = memories.filter(
    (m) => m.score == null || m.score >= config.searchThreshold,
  );
  return { contextInjection: formatRecalledMemories(relevant), memories: relevant };
}

// ---------------------------------------------------------------------------
// Auto-Recall: inject relevant memories before agent response
// ---------------------------------------------------------------------------

/**
 * Build context injection text from recalled memories.
 *
 * Separates memories into two sections:
 *   <kumiho_memory>  — cognitive memories (personal context, facts, decisions)
 *   <kumiho_project> — creative project items (past outputs, artifacts)
 *
 * The agent uses the project section to pick up where it left off on
 * creative tasks and to pass sourceMemoryKref when capturing new outputs.
 */
function formatRecalledMemories(memories: MemoryEntry[]): string {
  if (memories.length === 0) return "";

  // Split: memories whose space contains a non-personal segment are project items
  const cognitiveMemories: MemoryEntry[] = [];
  const projectMemories: MemoryEntry[] = [];

  for (const mem of memories) {
    const space = mem.space ?? "";
    // Heuristic: project memories live under spaces with 2+ segments
    // (e.g. "CognitiveMemory/blog-post-jan25") versus personal/session spaces
    const segments = space.split("/").filter(Boolean);
    const isProject =
      segments.length >= 2 &&
      !["personal", "users", "session", "work"].includes(segments[segments.length - 1]);

    if (isProject) {
      projectMemories.push(mem);
    } else {
      cognitiveMemories.push(mem);
    }
  }

  const sections: string[] = [];

  if (cognitiveMemories.length > 0) {
    const lines = [
      "<kumiho_memory>",
      "Relevant long-term memories for this conversation:",
      "",
    ];
    for (const mem of cognitiveMemories) {
      lines.push(`- [${mem.type}] ${mem.title}: ${mem.summary}`);
      if (mem.topics?.length) lines.push(`  Topics: ${mem.topics.join(", ")}`);
      lines.push(`  Kref: ${mem.kref}`);
    }
    lines.push("", "</kumiho_memory>");
    sections.push(lines.join("\n"));
  }

  if (projectMemories.length > 0) {
    const lines = [
      "<kumiho_project>",
      "Creative project items relevant to this conversation:",
      "Use `sourceMemoryKref` from these krefs when capturing new outputs derived from this work.",
      "",
    ];
    for (const mem of projectMemories) {
      lines.push(`- [${mem.type}] ${mem.title}: ${mem.summary}`);
      if (mem.topics?.length) lines.push(`  Tags: ${mem.topics.join(", ")}`);
      lines.push(`  Kref: ${mem.kref}`);
    }
    lines.push("", "</kumiho_project>");
    sections.push(lines.join("\n"));
  }

  return sections.join("\n\n");
}

export interface RecallResult {
  /** Context text to inject into the agent prompt. Empty if nothing recalled. */
  contextInjection: string;
  /** Memories that were recalled (for reference). */
  memories: MemoryEntry[];
  /** Session ID for continuing the conversation. */
  sessionId: string;
}

/**
 * Auto-recall hook: searches Kumiho for memories matching the user's message
 * and prepares context injection for the agent.
 */
export async function autoRecall(
  client: KumihoClient,
  config: ResolvedConfig,
  state: HookState,
  userMessage: string,
  channel?: ChannelInfo,
): Promise<RecallResult> {
  // Ensure we have a session ID
  if (!state.sessionId) {
    state.sessionId = await generateSessionId(config.userId);
  }

  state.lastUserMessage = userMessage;
  state.messageCount++;

  // Buffer message and retrieve memories in parallel — they're independent operations
  const channelMeta = channel ? buildChannelMetadata(channel) : {};
  const spacePaths = channel
    ? [getMemorySpace(channel, config.project)]
    : undefined;

  const [, memories] = await Promise.all([
    client.chatAdd(state.sessionId, "user", userMessage, {
      ...channelMeta,
      timestamp: new Date().toISOString(),
    }),
    client.memoryRetrieve({
      query: userMessage,
      limit: config.topK,
      spacePaths,
    }),
  ]);

  // Filter by similarity threshold
  const relevant = memories.filter(
    (m) => m.score == null || m.score >= config.searchThreshold,
  );

  state.recalledMemories = relevant;

  return {
    contextInjection: formatRecalledMemories(relevant),
    memories: relevant,
    sessionId: state.sessionId,
  };
}

// ---------------------------------------------------------------------------
// Auto-Capture: extract and store facts after agent response
// ---------------------------------------------------------------------------

export interface CaptureResult {
  /** Whether anything was captured and stored. */
  captured: boolean;
  /** Whether consolidation was triggered. */
  consolidated: boolean;
  /** Number of messages in current session after capture. */
  messageCount: number;
}

/**
 * Auto-capture hook: stores the assistant response in working memory and
 * checks if consolidation should be triggered.
 *
 * When localSummarization is enabled, the plugin uses the agent's LLM to
 * extract facts before sending to Kumiho Cloud (summaries only, no raw text).
 */
export async function autoCapture(
  client: KumihoClient,
  config: ResolvedConfig,
  state: HookState,
  redactor: PIIRedactor,
  artifacts: ArtifactManager,
  assistantResponse: string,
  channel?: ChannelInfo,
): Promise<CaptureResult> {
  if (!state.sessionId || !state.lastUserMessage) {
    return { captured: false, consolidated: false, messageCount: 0 };
  }

  state.lastAssistantResponse = assistantResponse;
  state.messageCount++;

  // Store assistant response in working memory
  await client.chatAdd(state.sessionId, "assistant", assistantResponse, {
    timestamp: new Date().toISOString(),
  });

  // Check if we should consolidate
  let consolidated = false;
  if (state.messageCount >= config.consolidationThreshold) {
    consolidated = await consolidateSession(
      client,
      config,
      state,
      redactor,
      artifacts,
      channel,
    );
  }

  return {
    captured: true,
    consolidated,
    messageCount: state.messageCount,
  };
}

// ---------------------------------------------------------------------------
// Session consolidation
// ---------------------------------------------------------------------------

/**
 * Consolidate the current session into long-term memory.
 *
 * 1. Fetch all messages from working memory
 * 2. Save raw conversation locally (artifact)
 * 3. Redact PII from summaries
 * 4. Store structured summary in Kumiho Cloud
 * 5. Clear working memory
 * 6. Start a new session
 */
export async function consolidateSession(
  client: KumihoClient,
  config: ResolvedConfig,
  state: HookState,
  redactor: PIIRedactor,
  artifacts: ArtifactManager,
  channel?: ChannelInfo,
): Promise<boolean> {
  if (!state.sessionId) return false;

  try {
    // 1. Fetch all messages
    const working = await client.chatGet(state.sessionId, 500);
    if (working.messages.length === 0) return false;

    // 2. Save raw conversation locally
    const artifact = await artifacts.saveConversation(
      config.project,
      state.sessionId,
      working.messages,
    );

    // 3. Build summary text from conversation
    const conversationText = working.messages
      .map((m) => `${m.role}: ${m.content}`)
      .join("\n");

    // 4. Redact PII
    let summaryText = `Consolidated ${working.message_count} messages from session ${state.sessionId}`;
    if (config.piiRedaction) {
      const redacted = redactor.redact(summaryText);
      summaryText = redactor.anonymizeSummary(redacted.text);
    }

    // 5. Determine space path
    const spaceHint = channel
      ? getMemorySpace(channel, config.project).replace(`${config.project}/`, "")
      : "personal";

    // 6. Store structured summary in Kumiho Cloud
    await client.memoryStore({
      type: "summary",
      title: `Session consolidation: ${state.sessionId}`,
      summary: summaryText,
      userText: config.privacy.uploadSummariesOnly ? undefined : conversationText,
      artifactLocation: artifact.location,
      spaceHint,
      tags: ["consolidated", "summary"],
      metadata: {
        session_id: state.sessionId,
        message_count: working.message_count,
        channels_used: channel ? [channel.platform] : [],
        artifact_hash: artifact.hash,
      },
    });

    // 7. Clear working memory
    await client.chatClear(state.sessionId);

    // 8. Start new session
    state.sessionId = await generateSessionId(config.userId, "personal", true);
    state.messageCount = 0;
    redactor.reset();

    return true;
  } catch (err) {
    // Consolidation failure should not break the conversation
    return false;
  }
}
