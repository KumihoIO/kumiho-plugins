/**
 * Cross-channel session ID management.
 *
 * Session IDs are user-centric, not channel-specific. A user chatting on
 * WhatsApp at 9 AM and continuing on Slack at 2 PM shares the same session.
 *
 * Format: {context}:user-{hash}:{date}:{sequence}
 * Example: alice-personal:user-7f3a9b1c2d:20260203:001
 */

import type { ChannelInfo, ChannelType } from "./types.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** SHA-256 hex hash (first 10 chars) of a stable user identifier. */
async function userHash(canonicalId: string): Promise<string> {
  const encoded = new TextEncoder().encode(canonicalId);
  const digest = await crypto.subtle.digest("SHA-256", encoded);
  const hex = Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return hex.slice(0, 10);
}

/** UTC date string in YYYYMMDD format. */
function utcDate(): string {
  const d = new Date();
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}${m}${day}`;
}

// ---------------------------------------------------------------------------
// Session sequence counter (in-memory, resets on restart)
//
// In production the kumiho-memory Python package delegates this to Redis
// (INCR). For the OpenClaw plugin we keep an in-memory counter per user per
// day. This is adequate because OpenClaw runs as a single process and
// sessions consolidate after N messages anyway.
// ---------------------------------------------------------------------------

const sequenceCounters = new Map<string, number>();

function nextSequence(userId: string, date: string): string {
  const key = `${userId}:${date}`;
  const seq = (sequenceCounters.get(key) ?? 0) + 1;
  sequenceCounters.set(key, seq);
  return String(seq).padStart(3, "0");
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Generate a channel-agnostic session ID.
 *
 * The same user talking on different channels within the same day gets the
 * same session prefix. A new sequence number is issued only when a fresh
 * session is explicitly requested (e.g. after consolidation).
 */
export async function generateSessionId(
  userId: string,
  context = "personal",
  newSession = false,
): Promise<string> {
  const hash = await userHash(userId);
  const date = utcDate();
  const key = `${context}:user-${hash}:${date}`;

  // Reuse the current session unless a new one is requested.
  if (!newSession) {
    const existing = sequenceCounters.get(`${userId}:${date}`);
    if (existing) {
      return `${key}:${String(existing).padStart(3, "0")}`;
    }
  }

  const seq = nextSequence(userId, date);
  return `${key}:${seq}`;
}

/**
 * Map a channel to its memory space path.
 *
 * Follows the same logic as `get_memory_space()` in kumiho-memory Python.
 */
export function getMemorySpace(
  channel: ChannelInfo,
  project = "CognitiveMemory",
): string {
  return channelTypeToSpace(channel.channelType, project, {
    teamSlug: channel.teamSlug,
    groupId: channel.groupId,
  });
}

export function channelTypeToSpace(
  channelType: ChannelType,
  project: string,
  opts?: { teamSlug?: string; groupId?: string },
): string {
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

/**
 * Infer ChannelType from an OpenClaw channel identifier.
 *
 * OpenClaw channels expose a `type` string like "whatsapp", "slack", etc.
 * We map group channels and workspace channels to the appropriate type.
 */
export function inferChannelType(
  _platform: string,
  isGroup?: boolean,
  isWorkspace?: boolean,
): ChannelType {
  if (isWorkspace) return "team_channel";
  if (isGroup) return "group_dm";
  return "personal_dm";
}

/**
 * Build channel metadata for storage in revision metadata.
 * Channel info is stored alongside summaries, not in the session ID.
 */
export function buildChannelMetadata(channel: ChannelInfo): Record<string, unknown> {
  return {
    channel: {
      platform: channel.platform,
      platform_user_id: channel.platformUserId,
      thread_id: channel.threadId,
      device: channel.device,
      timestamp: new Date().toISOString(),
    },
  };
}
