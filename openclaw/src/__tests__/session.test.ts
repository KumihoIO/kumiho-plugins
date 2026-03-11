import { describe, it, expect } from "vitest";
import { generateSessionId, getMemorySpace, channelTypeToSpace, inferChannelType } from "../session.js";
import type { ChannelInfo } from "../types.js";

// ---------------------------------------------------------------------------
// generateSessionId
// ---------------------------------------------------------------------------

describe("generateSessionId", () => {
  it("returns a string matching the expected format", async () => {
    const id = await generateSessionId("format-test-user");
    // Format: {context}:user-{10hexchars}:{YYYYMMDD}:{NNN}
    expect(id).toMatch(/^personal:user-[0-9a-f]{10}:\d{8}:\d{3}$/);
  });

  it("uses 'personal' as the default context", async () => {
    const id = await generateSessionId("default-context-user");
    expect(id.startsWith("personal:")).toBe(true);
  });

  it("uses the provided context when given", async () => {
    const id = await generateSessionId("context-test-user", "work");
    expect(id.startsWith("work:")).toBe(true);
  });

  it("returns the same session ID on repeated calls (session reuse)", async () => {
    const userId = "reuse-test-user-" + Math.random().toString(36).slice(2);
    const id1 = await generateSessionId(userId);
    const id2 = await generateSessionId(userId);
    expect(id1).toBe(id2);
  });

  it("generates a new session ID when newSession is true", async () => {
    const userId = "new-session-user-" + Math.random().toString(36).slice(2);
    const id1 = await generateSessionId(userId);
    const id2 = await generateSessionId(userId, "personal", true);
    expect(id2).not.toBe(id1);
    // New session should have a higher sequence number
    const seq1 = parseInt(id1.split(":")[3]);
    const seq2 = parseInt(id2.split(":")[3]);
    expect(seq2).toBeGreaterThan(seq1);
  });

  it("produces the same hash for the same userId (deterministic)", async () => {
    const userId = "deterministic-hash-user";
    const id1 = await generateSessionId(userId);
    const id2 = await generateSessionId(userId, "personal", true); // new session but same user
    // Hash is the second segment: "user-{hash}"
    const hash1 = id1.split(":")[1];
    const hash2 = id2.split(":")[1];
    expect(hash1).toBe(hash2);
  });

  it("produces different hashes for different userIds", async () => {
    const id1 = await generateSessionId("user-alpha-" + Math.random().toString(36));
    const id2 = await generateSessionId("user-beta-" + Math.random().toString(36));
    const hash1 = id1.split(":")[1];
    const hash2 = id2.split(":")[1];
    expect(hash1).not.toBe(hash2);
  });

  it("sequence number is zero-padded to 3 digits", async () => {
    const userId = "padded-seq-user-" + Math.random().toString(36).slice(2);
    const id = await generateSessionId(userId);
    const seq = id.split(":")[3];
    expect(seq).toMatch(/^\d{3}$/);
  });
});

// ---------------------------------------------------------------------------
// getMemorySpace / channelTypeToSpace
// ---------------------------------------------------------------------------

describe("getMemorySpace", () => {
  it("maps personal_dm to {project}/personal", () => {
    const channel: ChannelInfo = { platform: "telegram", channelType: "personal_dm" };
    expect(getMemorySpace(channel, "CognitiveMemory")).toBe("CognitiveMemory/personal");
  });

  it("maps team_channel to {project}/work/{teamSlug}", () => {
    const channel: ChannelInfo = { platform: "slack", channelType: "team_channel", teamSlug: "engineering" };
    expect(getMemorySpace(channel, "CognitiveMemory")).toBe("CognitiveMemory/work/engineering");
  });

  it("maps team_channel to {project}/work/default when teamSlug is absent", () => {
    const channel: ChannelInfo = { platform: "slack", channelType: "team_channel" };
    expect(getMemorySpace(channel, "CognitiveMemory")).toBe("CognitiveMemory/work/default");
  });

  it("maps group_dm to {project}/groups/{groupId}", () => {
    const channel: ChannelInfo = { platform: "telegram", channelType: "group_dm", groupId: "456" };
    expect(getMemorySpace(channel, "CognitiveMemory")).toBe("CognitiveMemory/groups/456");
  });

  it("maps group_dm to {project}/groups/default when groupId is absent", () => {
    const channel: ChannelInfo = { platform: "telegram", channelType: "group_dm" };
    expect(getMemorySpace(channel, "CognitiveMemory")).toBe("CognitiveMemory/groups/default");
  });

  it("reflects custom project name in the path", () => {
    const channel: ChannelInfo = { platform: "whatsapp", channelType: "personal_dm" };
    expect(getMemorySpace(channel, "MyProject")).toBe("MyProject/personal");
  });
});

describe("channelTypeToSpace", () => {
  it("defaults to personal for unknown channel types", () => {
    expect(channelTypeToSpace("personal_dm", "Proj")).toBe("Proj/personal");
  });
});

// ---------------------------------------------------------------------------
// inferChannelType
// ---------------------------------------------------------------------------

describe("inferChannelType", () => {
  it("returns team_channel when isWorkspace is true", () => {
    expect(inferChannelType("slack", false, true)).toBe("team_channel");
  });

  it("returns group_dm when isGroup is true", () => {
    expect(inferChannelType("telegram", true, false)).toBe("group_dm");
  });

  it("returns personal_dm when neither isGroup nor isWorkspace", () => {
    expect(inferChannelType("whatsapp", false, false)).toBe("personal_dm");
  });

  it("returns personal_dm when no flags are provided", () => {
    expect(inferChannelType("imessage")).toBe("personal_dm");
  });

  it("workspace flag takes precedence over group flag", () => {
    expect(inferChannelType("msteams", true, true)).toBe("team_channel");
  });
});
