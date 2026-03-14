// src/session.ts
async function userHash(canonicalId) {
  const encoded = new TextEncoder().encode(canonicalId);
  const digest = await crypto.subtle.digest("SHA-256", encoded);
  const hex = Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
  return hex.slice(0, 10);
}
function utcDate() {
  const d = /* @__PURE__ */ new Date();
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}${m}${day}`;
}
var sequenceCounters = /* @__PURE__ */ new Map();
function nextSequence(userId, date) {
  const key = `${userId}:${date}`;
  const seq = (sequenceCounters.get(key) ?? 0) + 1;
  sequenceCounters.set(key, seq);
  return String(seq).padStart(3, "0");
}
async function generateSessionId(userId, context = "personal", newSession = false) {
  const hash = await userHash(userId);
  const date = utcDate();
  const key = `${context}:user-${hash}:${date}`;
  if (!newSession) {
    const existing = sequenceCounters.get(`${userId}:${date}`);
    if (existing) {
      return `${key}:${String(existing).padStart(3, "0")}`;
    }
  }
  const seq = nextSequence(userId, date);
  return `${key}:${seq}`;
}
function getMemorySpace(channel, project = "CognitiveMemory") {
  return channelTypeToSpace(channel.channelType, project, {
    teamSlug: channel.teamSlug,
    groupId: channel.groupId
  });
}
function channelTypeToSpace(channelType, project, opts) {
  switch (channelType) {
    case "team_channel":
      return `${project}/work/${opts?.teamSlug ?? "default"}`;
    case "group_dm":
      return `${project}/groups/${opts?.groupId ?? "default"}`;
    case "personal_dm":
    default:
      return `${project}/personal`;
  }
}
function buildChannelMetadata(channel) {
  return {
    channel: {
      platform: channel.platform,
      platform_user_id: channel.platformUserId,
      thread_id: channel.threadId,
      device: channel.device,
      timestamp: (/* @__PURE__ */ new Date()).toISOString()
    }
  };
}

// src/hooks.ts
function createHookState() {
  return {
    sessionId: null,
    lastUserMessage: null,
    lastAssistantResponse: null,
    messageCount: 0,
    recalledMemories: [],
    identityStoredFor: /* @__PURE__ */ new Set(),
    prefetchedRecall: null,
    memoryInstructionsInjected: false
  };
}
async function recordUserTurn(client, config, state, userMessage, channel) {
  if (!state.sessionId) {
    state.sessionId = await generateSessionId(config.userId);
  }
  state.lastUserMessage = userMessage;
  state.messageCount++;
  const channelMeta = channel ? buildChannelMetadata(channel) : {};
  await client.chatAdd(state.sessionId, "user", userMessage, {
    ...channelMeta,
    timestamp: (/* @__PURE__ */ new Date()).toISOString()
  });
}
async function prefetchMemories(client, config, query) {
  const memories = await client.memoryRetrieve({ query, limit: config.topK });
  const relevant = memories.filter(
    (m) => m.score == null || m.score >= config.searchThreshold
  );
  return { contextInjection: formatRecalledMemories(relevant), memories: relevant };
}
var MEMORY_AGENT_INSTRUCTIONS = [
  "<kumiho_instructions>",
  "You have Kumiho long-term memory \u2014 a persistent graph of the user's preferences, decisions, facts, and past work across conversations.",
  "",
  'Use `memory_search` proactively when the user asks about past decisions, preferences, prior work, or anything discussed before \u2014 never say "I don\'t remember" without searching first. Use `memory_store` when the user states a preference, decision, or correction, or when you produce a significant deliverable. Weave recalled context naturally without narrating the lookup. Use absolute dates when storing ("on Mar 8", not "today").',
  "</kumiho_instructions>"
].join("\n");
function formatRecalledMemories(memories, includeInstructions = false) {
  const sections = [];
  if (includeInstructions) {
    sections.push(MEMORY_AGENT_INSTRUCTIONS);
  }
  const cognitiveMemories = [];
  const projectMemories = [];
  for (const mem of memories) {
    const space = mem.space ?? "";
    const segments = space.split("/").filter(Boolean);
    const isProject = segments.length >= 2 && !["personal", "users", "session", "work"].includes(segments[segments.length - 1]);
    if (isProject) {
      projectMemories.push(mem);
    } else {
      cognitiveMemories.push(mem);
    }
  }
  if (cognitiveMemories.length > 0) {
    const lines = [
      "<kumiho_memory>",
      "Auto-recalled long-term memories from previous conversations. Treat as authoritative facts \u2014 use these to answer questions about the user's preferences, history, and prior decisions before relying on general knowledge.",
      ""
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
      ""
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
function buildRecallQuery(userMessage, state) {
  const parts = [userMessage.trim()];
  const wordCount = userMessage.trim().split(/\s+/).length;
  if (wordCount <= 6 && state.lastUserMessage) {
    parts.push(state.lastUserMessage.trim());
  }
  if (state.lastAssistantResponse) {
    const excerpt = state.lastAssistantResponse.trim().split(/\s+/).slice(0, 20).join(" ");
    parts.push(excerpt);
  }
  const seen = /* @__PURE__ */ new Set();
  const tokens = [];
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
async function autoRecall(client, config, state, userMessage, channel) {
  if (!state.sessionId) {
    state.sessionId = await generateSessionId(config.userId);
  }
  const recallQuery = buildRecallQuery(userMessage, state);
  state.lastUserMessage = userMessage;
  state.messageCount++;
  const channelMeta = channel ? buildChannelMetadata(channel) : {};
  const spacePaths = channel && channel.channelType !== "personal_dm" ? [getMemorySpace(channel, config.project)] : void 0;
  const [, memories] = await Promise.all([
    client.chatAdd(state.sessionId, "user", userMessage, {
      ...channelMeta,
      timestamp: (/* @__PURE__ */ new Date()).toISOString()
    }),
    client.memoryRetrieve({
      query: recallQuery,
      limit: config.topK,
      spacePaths
    })
  ]);
  const relevant = memories.filter(
    (m) => m.score == null || m.score >= config.searchThreshold
  );
  state.recalledMemories = relevant;
  const includeInstructions = !state.memoryInstructionsInjected;
  if (includeInstructions) state.memoryInstructionsInjected = true;
  return {
    contextInjection: formatRecalledMemories(relevant, includeInstructions),
    memories: relevant,
    sessionId: state.sessionId
  };
}
async function autoCapture(client, config, state, redactor, artifacts, assistantResponse, channel) {
  if (!state.sessionId || !state.lastUserMessage) {
    return { captured: false, consolidated: false, messageCount: 0 };
  }
  const trimmed = (assistantResponse ?? "").trim();
  if (!trimmed) {
    return { captured: false, consolidated: false, messageCount: state.messageCount };
  }
  state.lastAssistantResponse = trimmed;
  state.messageCount++;
  await client.chatAdd(state.sessionId, "assistant", trimmed, {
    timestamp: (/* @__PURE__ */ new Date()).toISOString()
  });
  let consolidated = false;
  if (state.messageCount >= config.consolidationThreshold) {
    consolidated = await consolidateSession(
      client,
      config,
      state,
      redactor,
      artifacts,
      channel
    );
  }
  return {
    captured: true,
    consolidated,
    messageCount: state.messageCount
  };
}
async function generateConsolidationSummary(config, messages) {
  if (!config.localSummarization) {
    return { summary: null, reason: "local summarization is disabled" };
  }
  const explicitProvider = config.consolidationModel.provider || config.llm.provider;
  const explicitApiKey = config.consolidationModel.apiKey || config.llm.apiKey;
  if (explicitProvider && !explicitApiKey && config.hostLlmProvider && explicitProvider !== config.hostLlmProvider) {
    return {
      summary: null,
      reason: `configured consolidation provider "${explicitProvider}" does not match the available host provider "${config.hostLlmProvider}"`
    };
  }
  const provider = explicitProvider || config.hostLlmProvider;
  const apiKey = explicitApiKey || config.hostLlmApiKey;
  const model = config.consolidationModel.model || config.llm.model;
  if (!provider || !apiKey) {
    return {
      summary: null,
      reason: "no LLM provider/API key was resolved for consolidation"
    };
  }
  const transcript = messages.filter((m) => m.role === "user" || m.role === "assistant").map((m) => `${m.role === "user" ? "User" : "Assistant"}: ${m.content}`).join("\n\n").slice(0, 8e3);
  const prompt = `Summarize the following conversation in 2-4 sentences. Focus on: key topics discussed, decisions made, important facts or preferences expressed by the user. Be concise \u2014 this summary will be stored as a long-term memory and recalled in future sessions.

<conversation>
${transcript}
</conversation>

Provide only the summary text, no labels or preamble.`;
  try {
    if (provider === "anthropic") {
      const res = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": apiKey,
          "anthropic-version": "2023-06-01"
        },
        body: JSON.stringify({
          model: model || "claude-haiku-4-5-20251001",
          max_tokens: 512,
          messages: [{ role: "user", content: prompt }]
        })
      });
      if (!res.ok) {
        return {
          summary: null,
          reason: `Anthropic summarization request failed with status ${res.status}`
        };
      }
      const data = await res.json();
      return {
        summary: data.content?.[0]?.text?.trim() ?? null,
        reason: "Anthropic response did not include summary text"
      };
    }
    if (provider === "openai") {
      const openAiModel = model || "gpt-4o-mini";
      if (/codex/i.test(openAiModel)) {
        const res2 = await fetch("https://api.openai.com/v1/responses", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${apiKey}`
          },
          body: JSON.stringify({
            model: openAiModel,
            input: prompt,
            max_output_tokens: 512
          })
        });
        if (!res2.ok) {
          return {
            summary: null,
            reason: `OpenAI summarization request failed with status ${res2.status}`
          };
        }
        const data2 = await res2.json();
        const text = data2.output_text?.trim() || data2.output?.flatMap((item) => item.content ?? []).map((item) => item.text?.trim() ?? "").find(Boolean) || null;
        return {
          summary: text,
          reason: "OpenAI response did not include summary text"
        };
      }
      const res = await fetch("https://api.openai.com/v1/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          model: openAiModel,
          max_tokens: 512,
          messages: [{ role: "user", content: prompt }]
        })
      });
      if (!res.ok) {
        return {
          summary: null,
          reason: `OpenAI summarization request failed with status ${res.status}`
        };
      }
      const data = await res.json();
      return {
        summary: data.choices?.[0]?.message?.content?.trim() ?? null,
        reason: "OpenAI response did not include summary text"
      };
    }
  } catch (err) {
    return {
      summary: null,
      reason: `LLM summarization request threw: ${err.message}`
    };
  }
  return {
    summary: null,
    reason: `unsupported LLM provider "${provider}" for consolidation`
  };
}
async function consolidateSession(client, config, state, redactor, artifacts, channel) {
  if (!state.sessionId) return false;
  try {
    const working = await client.chatGet(state.sessionId, 500);
    if (working.messages.length === 0) return false;
    const artifact = await artifacts.saveConversation(
      config.project,
      state.sessionId,
      working.messages
    );
    const userText = working.messages.filter((m) => m.role === "user").map((m) => m.content).join("\n");
    const assistantText = working.messages.filter((m) => m.role === "assistant").map((m) => m.content).join("\n");
    const { summary: llmSummary, reason: summaryFallbackReason } = await generateConsolidationSummary(config, working.messages);
    let summaryText = llmSummary ?? `Consolidated ${working.message_count} messages from session ${state.sessionId}`;
    if (!llmSummary && config.localSummarization) {
      console.warn(
        `[kumiho] consolidation for ${state.sessionId} fell back to static summary: ${summaryFallbackReason ?? "unknown reason"}`
      );
    }
    if (config.piiRedaction) {
      const redacted = redactor.redact(summaryText);
      summaryText = redactor.anonymizeSummary(redacted.text);
    }
    const spaceHint = channel ? getMemorySpace(channel, config.project).replace(`${config.project}/`, "") : "personal";
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
        artifact_hash: artifact.hash
      }
    });
    await client.chatClear(state.sessionId);
    state.sessionId = await generateSessionId(config.userId, "personal", true);
    state.messageCount = 0;
    redactor.reset();
    return true;
  } catch (err) {
    console.error(`[kumiho] consolidateSession failed: ${err.message}`);
    return false;
  }
}
export {
  autoCapture,
  autoRecall,
  buildRecallQuery,
  consolidateSession,
  createHookState,
  prefetchMemories,
  recordUserTurn
};
