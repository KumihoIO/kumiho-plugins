import type { IDataObject, INodeType, INodeTypeDescription, ITriggerFunctions, ITriggerResponse } from 'n8n-workflow';
import { sleep } from 'n8n-workflow';
import { getKumihoCredentials, getKumihoRequestHeaders } from '../helpers/kumihoApi';

type TriggerType = 'item' | 'revision' | 'artifact' | 'edge';
type StreamAction = 'created' | 'updated' | 'deleted' | 'tagged';

const matchesWildcardPattern = (valueRaw: string, patternRaw: string): boolean => {
  const value = (valueRaw ?? '').trim();
  const pattern = (patternRaw ?? '').trim();
  if (!pattern) return true;

  // Exact match if no wildcards.
  if (!pattern.includes('*') && !pattern.includes('?')) return value === pattern;

  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`^${escaped.replace(/\\\*/g, '.*').replace(/\\\?/g, '.')}$`);
  return re.test(value);
};

const normalizeContextPathToKrefFilter = (contextPathRaw: string): string => {
  const raw = (contextPathRaw ?? '').trim();
  if (!raw) return '';

  // If the user already provided a glob/pattern, don't second-guess it.
  if (raw.includes('*') || raw.includes('?')) return raw;

  const withScheme = raw.startsWith('kref://') ? raw : `kref://${raw.replace(/^\/+/, '')}`;
  const trimmed = withScheme.replace(/\/+$/, '');

  // Common intent: limit to a project/space subtree.
  return `${trimmed}/**`;
};

const getContextPrefixFromKrefFilter = (krefFilter: string): string => {
  const filter = (krefFilter ?? '').trim();
  if (!filter) return '';
  // Strip common subtree glob.
  if (filter.endsWith('/**')) return filter.slice(0, -3);
  // If filter contains wildcards, we can't reliably create a prefix.
  if (filter.includes('*') || filter.includes('?')) return '';
  return filter;
};

const matchesContextPath = (event: unknown, contextPathRaw: string): boolean => {
  const krefFilter = normalizeContextPathToKrefFilter(contextPathRaw);
  if (!krefFilter) return true;

  if (!event || typeof event !== 'object') return false;
  const evt = event as { kref?: string };
  const kref = String(evt.kref ?? '');
  if (!kref) return false;

  const prefix = getContextPrefixFromKrefFilter(krefFilter);
  if (!prefix) return true; // Pattern-based filter; rely on server-side filtering.

  // Ensure subtree semantics even if the user entered a bare project/space.
  if (kref === prefix) return true;
  if (kref.startsWith(prefix.endsWith('/') ? prefix : `${prefix}/`)) return true;
  return false;
};

const getKrefQueryParam = (kref: string, key: string): string | undefined => {
  const idx = kref.indexOf('?');
  if (idx === -1) return undefined;
  const query = kref.slice(idx + 1);
  for (const part of query.split('&')) {
    const [k, v] = part.split('=');
    if (k === key) return v;
  }
  return undefined;
};

const getKrefLastSegment = (kref: string): string => {
  const withoutQuery = kref.split('?')[0];
  const parts = withoutQuery.split('/').filter(Boolean);
  return parts.length ? parts[parts.length - 1] : withoutQuery;
};

const parseItemNameKindFromKref = (kref: string): { itemName?: string; itemKind?: string; itemNameKind?: string } => {
  const last = getKrefLastSegment(kref);
  if (!last) return {};

  // Common: itemName.kind
  const dotIndex = last.lastIndexOf('.');
  if (dotIndex > 0 && dotIndex < last.length - 1) {
    const itemName = last.slice(0, dotIndex);
    const itemKind = last.slice(dotIndex + 1);
    return { itemName, itemKind, itemNameKind: `${itemName}.${itemKind}` };
  }

  return { itemName: last, itemNameKind: last };
};

const getRoutingKeySegments = (routingKey: string): string[] => {
  return (routingKey ?? '')
    .trim()
    .split('.')
    .map((s) => s.trim())
    .filter(Boolean);
};

const matchesNameFilter = (event: unknown, triggerType: TriggerType, streamAction: StreamAction, filter: string): boolean => {
  const needle = filter.trim();
  if (!needle) return true;

  if (!event || typeof event !== 'object') return false;
  const evt = event as { kref?: string; routing_key?: string; details?: Record<string, unknown> };
  const kref = String(evt.kref ?? '');
  const routingKey = String(evt.routing_key ?? '');
  const details = (evt.details && typeof evt.details === 'object' ? evt.details : {}) as Record<string, unknown>;

  // If the user provides a full routing key pattern (contains '.'), match against routing_key.
  if (needle.includes('.')) {
    return matchesWildcardPattern(routingKey, needle);
  }

  // Type-specific interpretations (preferred; avoids confusing kref substring matches).
  if (triggerType === 'item') {
    // Expected: item.<name>.created/updated/deleted (e.g. item.image.created, item.metadata.updated)
    const segs = getRoutingKeySegments(routingKey);
    const typeSeg = segs[0];
    const nameSeg = segs.length >= 3 ? segs[1] : '';
    if (typeSeg === 'item' && nameSeg) return matchesWildcardPattern(nameSeg, needle);
    return false;
  }

  if (triggerType === 'revision' && streamAction === 'updated') {
    // Server emits e.g. revision.metadata.updated
    const segs = getRoutingKeySegments(routingKey);
    const typeSeg = segs[0];
    const nameSeg = segs.length >= 3 ? segs[1] : '';
    if (typeSeg === 'revision' && nameSeg) return matchesWildcardPattern(nameSeg, needle);
    return false;
  }

  if (triggerType === 'revision' && streamAction === 'tagged') {
    const tag = String(details.tag ?? details.tag_name ?? details.tagName ?? '');
    return matchesWildcardPattern(tag, needle);
  }

  if (triggerType === 'artifact') {
    const name = String(details.artifact_name ?? details.artifactName ?? details.name ?? '');
    if (name) return matchesWildcardPattern(name, needle);
    const a = getKrefQueryParam(kref, 'a');
    if (a) return matchesWildcardPattern(a, needle);
    return false;
  }

  if (triggerType === 'revision') {
    // If the user types a revision number, match kref query (?r=...)
    const r = getKrefQueryParam(kref, 'r');
    if (r && matchesWildcardPattern(r, needle)) return true;
    const n = String(details.revision_number ?? details.revisionNumber ?? details.number ?? '');
    if (n && matchesWildcardPattern(n, needle)) return true;
    return false;
  }

  return false;
};

export class KumihoEventStreamTrigger implements INodeType {
  description: INodeTypeDescription = {
    displayName: 'Kumiho Event Trigger',
    name: 'kumihoEventStreamTrigger',
    icon: 'file:../images/EventStream.svg',
    usableAsTool: true,
    group: ['trigger'],
    version: 1,
    description: 'Trigger workflows from Kumiho event streams.',
    defaults: {
      name: 'Kumiho Event Trigger'
    },
    inputs: [],
    outputs: ['main'],
    credentials: [
      {
        name: 'kumihoApi',
        required: true
      }
    ],
    properties: [
      {
        displayName: 'Trigger Type',
        name: 'triggerType',
        type: 'options',
        options: [
          { name: 'Artifact', value: 'artifact' },
          { name: 'Edge', value: 'edge' },
          { name: 'Item', value: 'item' },
          { name: 'Revision', value: 'revision' },
        ],
        default: 'revision',
      },
      {
        displayName: 'Stream Action',
        name: 'streamAction',
        type: 'options',
        options: [
          { name: 'Created', value: 'created' },
          { name: 'Updated', value: 'updated' },
          { name: 'Deleted', value: 'deleted' },
        ],
        default: 'created',
        displayOptions: {
          show: {
            triggerType: ['item', 'artifact', 'edge'],
          },
        },
      },
      {
        displayName: 'Stream Action',
        name: 'streamActionRevision',
        type: 'options',
        options: [
          { name: 'Created', value: 'created' },
          { name: 'Updated', value: 'updated' },
          { name: 'Deleted', value: 'deleted' },
          { name: 'Tagged', value: 'tagged' },
        ],
        default: 'created',
        displayOptions: {
          show: {
            triggerType: ['revision'],
          },
        },
      },
      {
        displayName: 'Path Filter (Project/Space)',
        name: 'contextPath',
        type: 'string',
        default: '',
        description: "Limit events to a project or space subtree (e.g. 'my-project', 'my-project/my-space', or 'kref://my-project/my-space/**')",
      },
      {
        displayName: 'Item Name Filter',
        name: 'itemNameFilter',
        type: 'string',
        default: '',
        description: "Optional. Further limit events to a specific item name extracted from kref (e.g. 'hero' from 'hero.image'). Supports '*' and '?' wildcards.",
      },
      {
        displayName: 'Item Kind Filter',
        name: 'itemKindFilter',
        type: 'string',
        default: '',
        description: "Optional. Further limit events to a specific item kind extracted from kref (e.g. 'image' from 'hero.image'). Supports '*' and '?' wildcards.",
      },
      {
        displayName: 'Advanced',
        name: 'advanced',
        type: 'collection',
        default: {},
        options: [
          {
            displayName: 'Cursor',
            name: 'cursor',
            type: 'string',
            default: '',
          },
          {
            displayName: 'Routing Key Name Filter',
            name: 'routingKeyNameFilter',
            type: 'string',
            default: '',
            description:
              "Item: matches the routing key name segment (e.g. 'image' in 'item.image.created'). Revision Tagged: matches tag name. Artifact: matches artifact name. Supports '*' and '?' wildcards, or provide a full routing key pattern like 'item.*.created'.",
          },
        ],
      },
      {
        displayName: 'Reconnect Delay (Seconds)',
        name: 'pollIntervalSeconds',
        type: 'number',
        default: 30,
      },
    ],
  };

  async trigger(this: ITriggerFunctions): Promise<ITriggerResponse> {
    const pollIntervalSeconds = this.getNodeParameter('pollIntervalSeconds') as number;
    const staticData = this.getWorkflowStaticData('node');
    const advanced = this.getNodeParameter('advanced') as { cursor?: string; routingKeyNameFilter?: string };
    let cursor = (advanced?.cursor as string) || (staticData.cursor as string);
    let isStopped = false;

    let abortController: AbortController | undefined;

    const connectSse = async (url: string, headers: Record<string, string>, onMessage: (payload: string) => void) => {
      abortController = new AbortController();

      try {
        const res = await fetch(url, {
          method: 'GET',
          headers: {
            Accept: 'text/event-stream',
            ...headers,
          },
          signal: abortController.signal,
        });

        if (!res.ok || !res.body) return;

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        const flush = () => {
          buffer = buffer.replace(/\r\n/g, '\n');

          while (true) {
            const splitIndex = buffer.indexOf('\n\n');
            if (splitIndex === -1) break;

            const rawEvent = buffer.slice(0, splitIndex);
            buffer = buffer.slice(splitIndex + 2);

            const lines = rawEvent.split('\n');
            const dataLines = lines.filter((line) => line.startsWith('data:'));
            if (!dataLines.length) continue;

            const data = dataLines
              .map((line) => line.slice('data:'.length).replace(/^\s*/, ''))
              .join('\n');
            if (!data) continue;

            onMessage(data);
          }
        };

        while (!isStopped) {
          const { done, value } = await reader.read();
          if (done) break;
          if (!value) continue;
          buffer += decoder.decode(value, { stream: true });
          flush();
        }
      } catch {
        // swallow connection errors; reconnect loop will handle retries
        return;
      }
    };

    const streamLoop = async () => {
      while (!isStopped) {
        const triggerType = this.getNodeParameter('triggerType') as TriggerType;
        const streamAction = (triggerType === 'revision'
          ? (this.getNodeParameter('streamActionRevision') as StreamAction)
          : (this.getNodeParameter('streamAction') as StreamAction));
        const routingKeyFilter =
          triggerType === 'item'
            ? streamAction === 'deleted'
              ? 'item.deleted,item.deprecated'
              : streamAction === 'updated'
                ? 'item.*.updated,item.metadata.updated'
                : `item.*.${streamAction}`
            : triggerType === 'artifact' && streamAction === 'deleted'
              ? 'artifact.deleted,artifact.deprecated'
              : triggerType === 'artifact' && streamAction === 'updated'
                ? 'artifact.*.updated,artifact.metadata.updated'
                : triggerType === 'revision' && streamAction === 'updated'
                  ? `revision.*.updated,revision.metadata.updated`
                  : triggerType === 'revision' && streamAction === 'deleted'
                    ? 'revision.deleted,revision.deprecated'
                    : `${triggerType}.${streamAction}`;
        const routingKeyNameFilter = String(advanced?.routingKeyNameFilter ?? '').trim();

        const contextPath = this.getNodeParameter('contextPath') as string;
        const krefFilter = normalizeContextPathToKrefFilter(contextPath);
        const itemNameFilter = String(this.getNodeParameter('itemNameFilter') as string).trim();
        const itemKindFilter = String(this.getNodeParameter('itemKindFilter') as string).trim();

        const creds = await getKumihoCredentials(this);
        const headers = await getKumihoRequestHeaders(this);

        const streamUrl = new URL(`${creds.baseUrl}/api/v1/events/stream`);
        streamUrl.searchParams.set('routing_key_filter', routingKeyFilter);
        if (krefFilter) streamUrl.searchParams.set('kref_filter', krefFilter);
        if (cursor) streamUrl.searchParams.set('cursor', cursor);

        await connectSse(streamUrl.toString(), headers, (payload: string) => {
          if (isStopped) return;

          let event: unknown;
          try {
            event = JSON.parse(payload);
          } catch {
            return;
          }

          if (!matchesContextPath(event, contextPath)) return;
          if (!matchesNameFilter(event, triggerType, streamAction, routingKeyNameFilter)) return;

          if ((itemNameFilter || itemKindFilter) && event && typeof event === 'object') {
            const evt = event as { kref?: string };
            const kref = String(evt.kref ?? '');
            const parsed = parseItemNameKindFromKref(kref);
            if (itemNameFilter && !matchesWildcardPattern(parsed.itemName ?? '', itemNameFilter)) return;
            if (itemKindFilter && !matchesWildcardPattern(parsed.itemKind ?? '', itemKindFilter)) return;
          }

          if (event && typeof event === 'object' && 'cursor' in event) {
            const eventCursor = (event as { cursor?: string }).cursor;
            if (eventCursor) {
              cursor = eventCursor;
              staticData.cursor = eventCursor;
            }
          }

          this.emit([[{ json: event as IDataObject }]]);
        });

        // Reconnect delay (re-uses the existing Poll Interval field).
        await sleep(Math.max(1, pollIntervalSeconds) * 1000);
      }
    };

    void streamLoop();

    return {
      closeFunction: async () => {
        isStopped = true;
        try {
          abortController?.abort();
        } catch {
          // ignore
        }
      }
    };
  }
}




