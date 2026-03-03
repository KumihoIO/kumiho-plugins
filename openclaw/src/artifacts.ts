/**
 * Local artifact file management.
 *
 * Raw conversation logs, voice recordings, images, and other media are
 * stored locally. Only artifact *pointers* (path + hash + size) are sent
 * to Kumiho Cloud.
 */

import { createHash } from "node:crypto";
import { mkdir, writeFile, stat, readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

import type { ArtifactPointer, ChatMessage } from "./types.js";

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

const DEFAULT_ARTIFACT_ROOT = join(homedir(), ".kumiho", "artifacts");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function ensureDir(dir: string): Promise<void> {
  await mkdir(dir, { recursive: true });
}

function sha256(content: string | Buffer): string {
  return createHash("sha256").update(content).digest("hex");
}

// ---------------------------------------------------------------------------
// Artifact manager
// ---------------------------------------------------------------------------

export class ArtifactManager {
  private readonly root: string;

  constructor(artifactDir?: string) {
    this.root = artifactDir ?? DEFAULT_ARTIFACT_ROOT;
  }

  /**
   * Save a conversation transcript as a local Markdown file.
   * Returns an artifact pointer for Kumiho metadata.
   */
  async saveConversation(
    project: string,
    sessionId: string,
    messages: ChatMessage[],
    summary?: string,
  ): Promise<ArtifactPointer> {
    const space = "sessions";
    const dir = join(this.root, project, space);
    await ensureDir(dir);

    const filename = `${sessionId.replace(/:/g, "-")}.md`;
    const filePath = join(dir, filename);

    // Build Markdown content
    const lines: string[] = [
      `# Session: ${sessionId}`,
      "",
      `**Messages**: ${messages.length}`,
      `**Created**: ${new Date().toISOString()}`,
      "",
      "---",
      "",
    ];

    if (summary) {
      lines.push("## Summary", "", summary, "", "---", "");
    }

    lines.push("## Conversation", "");

    for (const msg of messages) {
      const role = msg.role === "user" ? "User" : "Assistant";
      lines.push(`### ${role} (${msg.timestamp})`, "", msg.content, "");
    }

    const content = lines.join("\n");
    await writeFile(filePath, content, "utf-8");

    const hash = sha256(content);
    const fileStats = await stat(filePath);

    return {
      type: "chat_transcript",
      storage: "local",
      location: `file://${filePath}`,
      hash: `sha256:${hash}`,
      size_bytes: fileStats.size,
      metadata: {
        message_count: messages.length,
        session_id: sessionId,
      },
    };
  }

  /**
   * Save a tool execution log as a local file.
   */
  async saveExecutionLog(
    project: string,
    task: string,
    params: {
      status: string;
      exitCode?: number;
      durationMs?: number;
      stdout?: string;
      stderr?: string;
    },
  ): Promise<ArtifactPointer> {
    const dir = join(this.root, project, "executions");
    await ensureDir(dir);

    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    const filename = `exec-${timestamp}.md`;
    const filePath = join(dir, filename);

    const lines = [
      `# Execution: ${task}`,
      "",
      `**Status**: ${params.status}`,
      `**Exit Code**: ${params.exitCode ?? "N/A"}`,
      `**Duration**: ${params.durationMs != null ? `${params.durationMs}ms` : "N/A"}`,
      `**Timestamp**: ${new Date().toISOString()}`,
      "",
    ];

    if (params.stdout) {
      lines.push("## stdout", "", "```", params.stdout, "```", "");
    }

    if (params.stderr) {
      lines.push("## stderr", "", "```", params.stderr, "```", "");
    }

    const content = lines.join("\n");
    await writeFile(filePath, content, "utf-8");

    const hash = sha256(content);
    const fileStats = await stat(filePath);

    return {
      type: "execution_log",
      storage: "local",
      location: `file://${filePath}`,
      hash: `sha256:${hash}`,
      size_bytes: fileStats.size,
      metadata: {
        task,
        status: params.status,
        exit_code: params.exitCode,
      },
    };
  }

  /**
   * Copy an attachment into the artifact directory and return a pointer.
   */
  async saveAttachment(
    project: string,
    sourcePath: string,
    mimeType: string,
    description?: string,
  ): Promise<ArtifactPointer> {
    const dir = join(this.root, project, "attachments");
    await ensureDir(dir);

    const sourceContent = await readFile(sourcePath);
    const hash = sha256(sourceContent);

    // Preserve original filename
    const ext = sourcePath.split(".").pop() ?? "bin";
    const filename = `${hash.slice(0, 16)}.${ext}`;
    const destPath = join(dir, filename);

    await writeFile(destPath, sourceContent);
    const fileStats = await stat(destPath);

    return {
      type: mimeType.startsWith("image/")
        ? "image"
        : mimeType.startsWith("audio/")
          ? "voice_recording"
          : mimeType.startsWith("video/")
            ? "video"
            : "document",
      storage: "local",
      location: `file://${destPath}`,
      hash: `sha256:${hash}`,
      size_bytes: fileStats.size,
      metadata: {
        mime_type: mimeType,
        original_path: sourcePath,
        description,
      },
    };
  }

  /**
   * Get the local artifact directory path for a project.
   */
  getProjectDir(project: string): string {
    return join(this.root, project);
  }
}
