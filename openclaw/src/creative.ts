/**
 * Creative Memory handlers for OpenClaw.
 *
 * Two tools:
 *
 *   creative_capture — Fire-and-forget: sends a creative artifact to the
 *   kumiho-FastAPI BFF which runs a 7-step graph pipeline in the background
 *   (ensureSpace → createItem → createRevision → createArtifact →
 *   createEdge → memoryStore → discoverEdges).  The agent turn is never
 *   blocked — the tool returns a job ID immediately.
 *
 *   project_recall — Search creative items in a project space.  Returns
 *   a formatted list of past outputs so the agent can reference previous
 *   work when continuing a project.
 *
 * Design note on sourceMemoryKref:
 *   When the agent captures a creative output, it should pass the kref of
 *   the recalled memory that inspired or drove the output.  This kref is
 *   stored on the BFF request so the graph pipeline can create a
 *   DERIVED_FROM edge linking the creative artifact back to the cognitive
 *   memory — making provenance traversal trivial later.
 */

import type { KumihoClient } from "./client.js";
import type {
  ResolvedConfig,
  CreativeCaptureParams,
  CreativeKind,
  ProjectRecallParams,
} from "./types.js";

export interface CreativeToolContext {
  client: KumihoClient;
  config: ResolvedConfig;
  logger: { info: (msg: string) => void; error: (msg: string) => void };
}

// ---------------------------------------------------------------------------
// creative_capture
// ---------------------------------------------------------------------------

/**
 * Validate and normalise the kind value from tool params.
 * Falls back to "other" for unrecognised strings.
 */
function coerceKind(raw: unknown): CreativeKind {
  const KINDS: CreativeKind[] = [
    "document",
    "code",
    "design",
    "plan",
    "analysis",
    "other",
  ];
  if (typeof raw === "string" && KINDS.includes(raw as CreativeKind)) {
    return raw as CreativeKind;
  }
  return "other";
}

/**
 * Handle the creative_capture tool.
 *
 * Fires an async job to the kumiho-FastAPI BFF and returns immediately with
 * the job ID.  The BFF runs the full graph pipeline without blocking the
 * agent turn.
 */
export async function creativeCaptureHandler(
  ctx: CreativeToolContext,
  params: Partial<CreativeCaptureParams>,
): Promise<string> {
  // Validate required fields
  const title = (params.title ?? "").trim();
  const content = (params.content ?? "").trim();
  const project = (params.project ?? "").trim();

  if (!title) return "creative_capture: `title` is required.";
  if (!content) return "creative_capture: `content` is required.";
  if (!project) return "creative_capture: `project` (space slug) is required.";

  const bffUrl = ctx.config.bffEndpoint;
  if (!bffUrl) {
    return (
      "Creative capture is not configured: `bffEndpoint` is missing.\n" +
      "Set `bffEndpoint` in the Kumiho plugin config to point at your kumiho-FastAPI instance."
    );
  }

  const captureParams: CreativeCaptureParams = {
    title,
    content,
    kind: coerceKind(params.kind),
    project,
    tags: Array.isArray(params.tags) ? params.tags : [],
    sourceMemoryKref: params.sourceMemoryKref,
    metadata: params.metadata ?? {},
  };

  try {
    const result = await ctx.client.creativeEnqueue(
      captureParams,
      bffUrl,
      ctx.config.apiKey,
    );

    ctx.logger.info(
      `Creative capture queued: "${title}" → ${project} (job: ${result.jobId})`,
    );

    return [
      `Creative capture queued.`,
      ``,
      `Job ID : ${result.jobId}`,
      `Title  : "${title}"`,
      `Kind   : ${captureParams.kind}`,
      `Project: ${project}`,
      params.sourceMemoryKref
        ? `Linked to memory kref: ${params.sourceMemoryKref}`
        : "",
      ``,
      `The content is being processed and linked to your memory graph in the background.`,
      `Use the job ID to poll status: GET /api/v1/apps/creative/jobs/${result.jobId}`,
    ]
      .filter((l) => l !== undefined)
      .join("\n");
  } catch (err) {
    const msg = (err as Error).message;
    ctx.logger.error(`creative_capture failed: ${msg}`);
    return `Creative capture failed: ${msg}`;
  }
}

// ---------------------------------------------------------------------------
// project_recall
// ---------------------------------------------------------------------------

/**
 * Handle the project_recall tool.
 *
 * Searches Kumiho long-term memory for creative outputs in the specified
 * project space.  Results are formatted as a structured list the agent can
 * reference when continuing a project.
 */
export async function projectRecallHandler(
  ctx: CreativeToolContext,
  params: Partial<ProjectRecallParams>,
): Promise<string> {
  const project = (params.project ?? "").trim();
  if (!project) return "project_recall: `project` (space slug) is required.";

  const kind = coerceKind(params.kind ?? "other");
  const query =
    params.query?.trim() ||
    [project, params.kind ? kind : ""].filter(Boolean).join(" ");
  const limit = params.limit ?? ctx.config.topK;

  // Scope recall to this project's space
  const spacePaths = [`${ctx.config.project}/${project}`];

  try {
    const results = await ctx.client.memoryRetrieve({
      query,
      limit,
      spacePaths,
    });

    if (results.length === 0) {
      return `No creative items found in project "${project}". Capture something first using creative_capture.`;
    }

    const lines: string[] = [`## Project: ${project}`, ""];

    for (const item of results) {
      lines.push(`### ${item.title || "(untitled)"}`);
      if (item.summary) lines.push(item.summary);
      if (item.topics?.length) lines.push(`Tags: ${item.topics.join(", ")}`);
      if (item.score != null) lines.push(`Relevance: ${item.score.toFixed(2)}`);
      lines.push(`Kref: ${item.kref}`);
      lines.push("");
    }

    return lines.join("\n");
  } catch (err) {
    const msg = (err as Error).message;
    ctx.logger.error(`project_recall failed: ${msg}`);
    return `Project recall failed: ${msg}`;
  }
}
