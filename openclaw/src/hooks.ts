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
import type { ResolvedConfig, MemoryEntry, ChannelInfo, ChatMessage } from "./types.js";
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
  /** @internal Whether the first-turn memory instructions have been injected. */
  memoryInstructionsInjected: boolean;
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
    memoryInstructionsInjected: false,
  };
}

/**
 * Record the user's turn in the session buffer and update hook state.
 *
 * This is the write-only counterpart to prefetchMemories. In the
 * stale-while-revalidate path, before_prompt_build uses a prefetched recall
 * result and skips autoRecall — but autoRecall is the only place that calls
 * chatAdd for the user message and bumps messageCount. Without this call,
 * every turn after the first silently drops the user message from the buffer,
 * producing session artifacts that contain only assistant turns.
 */
export async function recordUserTurn(
  client: KumihoClient,
  config: ResolvedConfig,
  state: HookState,
  userMessage: string,
  channel?: ChannelInfo,
): Promise<void> {
  if (!state.sessionId) {
    state.sessionId = await generateSessionId(config.userId);
  }
  state.lastUserMessage = userMessage;
  state.messageCount++;
  const channelMeta = channel ? buildChannelMetadata(channel) : {};
  await client.chatAdd(state.sessionId, "user", userMessage, {
    ...channelMeta,
    timestamp: new Date().toISOString(),
  });
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
/**
 * One-shot instruction block injected only on the first turn of a session.
 * Teaches the agent how to use Kumiho memory tools proactively.
 */
const MEMORY_AGENT_INSTRUCTIONS = [
  "<kumiho_instructions>",
  "You have Kumiho long-term memory — a persistent graph of the user's preferences, decisions, facts, and past work across conversations.",
  "",
  "Use `memory_search` proactively when the user asks about past decisions, preferences, prior work, or anything discussed before — never say \"I don't remember\" without searching first. Use `memory_store` when the user states a preference, decision, or correction, or when you produce a significant deliverable. Weave recalled context naturally without narrating the lookup. Use absolute dates when storing (\"on Mar 8\", not \"today\").",
  "</kumiho_instructions>",
].join("\n");

function formatRecalledMemories(memories: MemoryEntry[], includeInstructions = false): string {
  const sections: string[] = [];

  if (includeInstructions) {
    sections.push(MEMORY_AGENT_INSTRUCTIONS);
  }

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

  if (cognitiveMemories.length > 0) {
    const lines = [
      "<kumiho_memory>",
      "Auto-recalled long-term memories from previous conversations. Treat as authoritative facts — use these to answer questions about the user's preferences, history, and prior decisions before relying on general knowledge.",
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
 /**
 * Build a context-enriched recall query by combining the current user message
 * with key terms extracted from recent conversation turns.
 *
 * Short messages like "yeah", "what about that?", "tell me more" carry little
 * semantic signal on their own. Appending the prior user message + last
 * assistant response gives the retrieval a meaningful anchor without blowing
 * up the query length.
 *
 * Strategy:
 *   - Always include the current message.
 *   - If it's short (<= 6 words), append the previous user message as context.
 *   - Append a 20-word excerpt of the last assistant response (highest-signal
 *     terms tend to appear early in the response).
 *   - Deduplicate and cap total query at ~200 chars to stay within index limits.
 */
export function buildRecallQuery(
  userMessage: string,
  state: Pick<HookState, "lastUserMessage" | "lastAssistantResponse">,
): string {
  const parts: string[] = [userMessage.trim()];

  const wordCount = userMessage.trim().split(/\s+/).length;

  // Short / ambiguous message — pull in previous user turn for topic context
  if (wordCount <= 6 && state.lastUserMessage) {
    parts.push(state.lastUserMessage.trim());
  }

  // Prepend key terms from last assistant response (first 20 words)
  if (state.lastAssistantResponse) {
    const excerpt = state.lastAssistantResponse
      .trim()
      .split(/\s+/)
      .slice(0, 20)
      .join(" ");
    parts.push(excerpt);
  }

  // Deduplicate words and cap at 200 chars
  const seen = new Set<string>();
  const tokens: string[] = [];
  for (const part of parts) {
    for (const word of part.split(/\s+/)) {
      const w = word.toLowerCase().replace(/[^\w]/g, "");
      if (w.length > 2 && !seen.has(w)) {
        seen.add(w);
        tokens.push(word);
      }
    }
  }

  return tokens.join(" ").slice(0, 200);
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

  // Build context-enriched query before updating state (so we capture the
  // *previous* turn's context, not the current message overwriting it)
  const recallQuery = buildRecallQuery(userMessage, state);

  state.lastUserMessage = userMessage;
  state.messageCount++;

  // Buffer message and retrieve memories in parallel — they're independent operations
  const channelMeta = channel ? buildChannelMetadata(channel) : {};
  // For personal DMs, search across all spaces — the user's work memories
  // (e.g. CognitiveMemory/work/*) are just as relevant as personal memories.
  // Only restrict space for group/team channels where isolation is needed.
  const spacePaths =
    channel && channel.channelType !== "personal_dm"
      ? [getMemorySpace(channel, config.project)]
      : undefined;

  const [, memories] = await Promise.all([
    client.chatAdd(state.sessionId, "user", userMessage, {
      ...channelMeta,
      timestamp: new Date().toISOString(),
    }),
    client.memoryRetrieve({
      query: recallQuery,
      limit: config.topK,
      spacePaths,
    }),
  ]);

  // Filter by similarity threshold
  const relevant = memories.filter(
    (m) => m.score == null || m.score >= config.searchThreshold,
  );

  state.recalledMemories = relevant;

  // Inject memory instructions on the first turn only
  const includeInstructions = !state.memoryInstructionsInjected;
  if (includeInstructions) state.memoryInstructionsInjected = true;

  return {
    contextInjection: formatRecalledMemories(relevant, includeInstructions),
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

  // Skip empty/whitespace-only responses (e.g. tool-only turns)
  const trimmed = (assistantResponse ?? "").trim();
  if (!trimmed) {
    return { captured: false, consolidated: false, messageCount: state.messageCount };
  }

  state.lastAssistantResponse = trimmed;
  state.messageCount++;

  // Store assistant response in working memory
  await client.chatAdd(state.sessionId, "assistant", trimmed, {
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
// LLM-based session summarization
// ---------------------------------------------------------------------------

/**
 * Call the configured LLM to generate a conversation summary.
 * Supports Anthropic and OpenAI APIs via direct fetch.
 * Returns a null summary with a reason when no LLM is configured or the call fails.
 */
async function generateConsolidationSummary(
  config: ResolvedConfig,
  messages: ChatMessage[],
): Promise<{ summary: string | null; reason?: string }> {
  if (!config.localSummarization) {
    return { summary: null, reason: "local summarization is disabled" };
  }

  const explicitProvider =
    config.consolidationModel.provider ||
    config.llm.provider;
  const explicitApiKey =
    config.consolidationModel.apiKey ||
    config.llm.apiKey;
  if (
    explicitProvider &&
    !explicitApiKey &&
    config.hostLlmProvider &&
    explicitProvider !== config.hostLlmProvider
  ) {
    return {
      summary: null,
      reason:
        `configured consolidation provider "${explicitProvider}" does not match ` +
        `the available host provider "${config.hostLlmProvider}"`,
    };
  }

  const provider = explicitProvider || config.hostLlmProvider;
  const apiKey = explicitApiKey || config.hostLlmApiKey;
  const model =
    config.consolidationModel.model ||
    config.llm.model;

  if (!provider || !apiKey) {
    return {
      summary: null,
      reason: "no LLM provider/API key was resolved for consolidation",
    };
  }

  // Build interleaved transcript (cap at 8000 chars to stay within token limits)
  const transcript = messages
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => `${m.role === "user" ? "User" : "Assistant"}: ${m.content}`)
    .join("\n\n")
    .slice(0, 8000);

  const prompt =
    `Summarize the following conversation in 2-4 sentences. Focus on: key topics discussed, ` +
    `decisions made, important facts or preferences expressed by the user. Be concise — this ` +
    `summary will be stored as a long-term memory and recalled in future sessions.\n\n` +
    `<conversation>\n${transcript}\n</conversation>\n\n` +
    `Provide only the summary text, no labels or preamble.`;

  try {
    if (provider === "anthropic") {
      const res = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": apiKey,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify({
          model: model || "claude-haiku-4-5-20251001",
          max_tokens: 512,
          messages: [{ role: "user", content: prompt }],
        }),
      });
      if (!res.ok) {
        return {
          summary: null,
          reason: `Anthropic summarization request failed with status ${res.status}`,
        };
      }
      const data = await res.json() as { content?: Array<{ text?: string }> };
      return {
        summary: data.content?.[0]?.text?.trim() ?? null,
        reason: "Anthropic response did not include summary text",
      };
    }

    if (provider === "openai") {
      const openAiModel = model || "gpt-4o-mini";
      if (/codex/i.test(openAiModel)) {
        const res = await fetch("https://api.openai.com/v1/responses", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${apiKey}`,
          },
          body: JSON.stringify({
            model: openAiModel,
            input: prompt,
            max_output_tokens: 512,
          }),
        });
        if (!res.ok) {
          return {
            summary: null,
            reason: `OpenAI summarization request failed with status ${res.status}`,
          };
        }
        const data = await res.json() as {
          output_text?: string;
          output?: Array<{ content?: Array<{ text?: string }> }>;
        };
        const text =
          data.output_text?.trim() ||
          data.output?.flatMap((item) => item.content ?? []).map((item) => item.text?.trim() ?? "").find(Boolean) ||
          null;
        return {
          summary: text,
          reason: "OpenAI response did not include summary text",
        };
      }

      const res = await fetch("https://api.openai.com/v1/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          model: openAiModel,
          max_tokens: 512,
          messages: [{ role: "user", content: prompt }],
        }),
      });
      if (!res.ok) {
        return {
          summary: null,
          reason: `OpenAI summarization request failed with status ${res.status}`,
        };
      }
      const data = await res.json() as { choices?: Array<{ message?: { content?: string } }> };
      return {
        summary: data.choices?.[0]?.message?.content?.trim() ?? null,
        reason: "OpenAI response did not include summary text",
      };
    }
  } catch (err) {
    return {
      summary: null,
      reason: `LLM summarization request threw: ${(err as Error).message}`,
    };
  }

  return {
    summary: null,
    reason: `unsupported LLM provider "${provider}" for consolidation`,
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

    // 3. Split messages by role
    const userText = working.messages
      .filter((m) => m.role === "user")
      .map((m) => m.content)
      .join("\n");
    const assistantText = working.messages
      .filter((m) => m.role === "assistant")
      .map((m) => m.content)
      .join("\n");

    // 4. Generate LLM summary (falls back to static if localSummarization is off or LLM unavailable)
    const { summary: llmSummary, reason: summaryFallbackReason } =
      await generateConsolidationSummary(config, working.messages);
    let summaryText = llmSummary ?? `Consolidated ${working.message_count} messages from session ${state.sessionId}`;
    if (!llmSummary && config.localSummarization) {
      console.warn(
        `[kumiho] consolidation for ${state.sessionId} fell back to static summary: ` +
          `${summaryFallbackReason ?? "unknown reason"}`,
      );
    }
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
      userText: config.privacy.uploadSummariesOnly ? summaryText : userText,
      assistantText: config.privacy.uploadSummariesOnly ? "" : assistantText,
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
    // Consolidation failure should not break the conversation, but log it
    // so failures aren't silently swallowed.
    console.error(`[kumiho] consolidateSession failed: ${(err as Error).message}`);
    return false;
  }
}
