import type {
  IDataObject,
  IExecuteFunctions,
  INodeExecutionData,
  INodeType,
  INodeTypeDescription,
} from 'n8n-workflow';

function stripProjectMetadata(value: unknown): unknown {
  if (!value) return value;
  if (Array.isArray(value)) {
    return value.map((entry) => stripProjectMetadata(entry));
  }
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    if (!Object.prototype.hasOwnProperty.call(obj, 'metadata')) return value;
    const rest: Record<string, unknown> = { ...obj };
    delete rest.metadata;
    return rest;
  }
  return value;
}
import { ApplicationError, NodeOperationError } from 'n8n-workflow';

import { applyReturnAllLimit, kumihoRequest, normalizeMetadata } from '../helpers/kumihoApi';

type OutputFormat = 'split' | 'singleArray';

type Resource = 'project' | 'space' | 'item' | 'revision' | 'artifact' | 'bundle' | 'graph' | 'kref';

type CrudOperation = 'create' | 'read' | 'update' | 'delete';

type ReadModeProject = 'projectGet' | 'projectList';
type ReadModeSpace = 'spaceGet' | 'spaceList';
type ReadModeItem = 'itemGet' | 'itemGetByPath' | 'itemSearch';
type ReadModeRevision =
  | 'revisionGetByKref'
  | 'revisionGetByTag'
  | 'revisionGetAsOf'
  | 'revisionListArtifacts'
  | 'revisionListTags'
  | 'revisionHasTag'
  | 'revisionWasTagged'
  | 'graphListEdges'
  | 'graphGetDependencies'
  | 'graphFindPath'
  | 'graphAnalyzeImpact';
type ReadModeArtifact = 'artifactGet' | 'artifactGetByLocation';
type ReadModeBundle = 'bundleGet' | 'bundleListMembers' | 'bundleHistory';
type ReadModeGraph = 'graphListEdges' | 'graphGetDependencies' | 'graphFindPath' | 'graphAnalyzeImpact';
type ReadModeKref = 'krefResolve';

type CreateMode = 'graphCreateEdge';
type CreateModeRevision = 'revisionCreate' | 'graphCreateEdge';

type UpdateModeItem = 'itemUpdateMetadata' | 'itemSetAttribute' | 'itemDeprecate';
type UpdateModeRevision = 'revisionUpdateMetadata' | 'revisionDeprecate' | 'revisionSetTag' | 'revisionRemoveTag';
type UpdateModeArtifact = 'artifactUpdateMetadata' | 'artifactDeprecate';
type UpdateModeBundle = 'bundleAddMember' | 'bundleRemoveMember';

const splitCsv = (value: unknown): string[] | undefined => {
  const raw = String(value ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  return raw.length ? raw : undefined;
};

const splitKrefList = (value: unknown): string[] => {
  if (Array.isArray(value)) {
    return value
      .map((v) => String(v ?? '').trim())
      .filter(Boolean);
  }

  const raw = String(value ?? '').trim();
  if (!raw) return [];

  // Allow JSON array input (useful when piping data through nodes)
  if (raw.startsWith('[')) {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        return parsed
          .map((v) => String(v ?? '').trim())
          .filter(Boolean);
      }
    } catch {
      // fall through to delimiter splitting
    }
  }

  // Allow newline- or comma-separated lists
  return raw
    .split(/[\r\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
};

const parseOptionalInt = (value: unknown): number | undefined => {
  const raw = String(value ?? '').trim();
  if (!raw) return undefined;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || !Number.isInteger(parsed) || parsed < 0) {
    throw new ApplicationError(`Value must be a non-negative integer (got '${raw}')`);
  }
  return parsed;
};

const normalizeProjectName = (context: IExecuteFunctions, raw: unknown): string => {
  const input = String(raw ?? '').trim();
  if (!input) {
    throw new NodeOperationError(context.getNode(), 'Project Name is required');
  }

  // Users often paste paths like "/project". Projects are identified by a slug/name, not a path.
  const normalized = input.replace(/^\/+/, '');
  if (!normalized) {
    throw new NodeOperationError(context.getNode(), 'Project Name is required');
  }
  if (normalized.includes('/')) {
    throw new NodeOperationError(
      context.getNode(),
      'Project Name must be a single URL-safe name (no "/"). Example: my-vfx-project',
    );
  }

  return normalized;
};

const normalizeKumihoPath = (context: IExecuteFunctions, raw: unknown, fieldLabel: string): string => {
  const input = String(raw ?? '').trim();
  if (!input) {
    throw new NodeOperationError(context.getNode(), `${fieldLabel} is required`);
  }

  // Common user mistake: pasting handlebars-style templates instead of n8n expressions.
  if (input.includes('{{') || input.includes('}}') || input.startsWith('={{') || input.includes('$json')) {
    throw new NodeOperationError(
      context.getNode(),
      `${fieldLabel} looks like a template string ('{{ ... }}'). In n8n use an expression like: ={{ $json.name }}`,
    );
  }

  // Accept either "/project/space" or "project/space" and normalize to leading '/'.
  const withoutScheme = input.replace(/^[a-z]+:\/\/[^/]+/i, '').trim();
  const noLeading = withoutScheme.replace(/^\/+/, '');
  const noTrailing = noLeading.replace(/\/+$/, '');
  if (!noTrailing) {
    throw new NodeOperationError(context.getNode(), `${fieldLabel} is required`);
  }

  // Guardrail: only allow URL-safe path segments.
  // Examples: /my-project, /my-project/Assets, /my-project/assets_v2
  const normalized = `/${noTrailing}`;
  const valid = /^\/[A-Za-z0-9][A-Za-z0-9._-]*(\/[A-Za-z0-9][A-Za-z0-9._-]*)*$/.test(normalized);
  if (!valid) {
    throw new NodeOperationError(
      context.getNode(),
      `${fieldLabel} must be a path like 'my-project' or 'my-project/Assets' (letters, numbers, '-', '_', '.' only). Got: '${input}'`,
    );
  }

  return normalized;
};

const normalizeSearchContextFilter = (context: IExecuteFunctions, raw: unknown): string => {
  const input = String(raw ?? '').trim();
  if (!input) return '';

  // Common user mistake: pasting handlebars-style templates instead of n8n expressions.
  // Keep the check, but do NOT enforce URL-safe segments (search supports wildcards).
  if (input.includes('{{') || input.includes('}}') || input.startsWith('={{') || input.includes('$json')) {
    throw new NodeOperationError(
      context.getNode(),
      "Context Filter looks like a template string ('{{ ... }}'). In n8n use an expression like: ={{ $json.path }}",
    );
  }

  // FastAPI search expects `context_filter` without a leading '/'.
  const withoutScheme = input.replace(/^[a-z]+:\/\/[^/]+/i, '').trim();
  const noLeading = withoutScheme.replace(/^\/+/, '');
  return noLeading.replace(/\/+$/, '');
};

const normalizeKrefString = (context: IExecuteFunctions, raw: unknown, fieldLabel: string): string => {
  const input = String(raw ?? '').trim();
  if (!input) {
    throw new NodeOperationError(context.getNode(), `${fieldLabel} is required`);
  }

  // Common user mistake: pasting handlebars-style templates instead of n8n expressions.
  if (input.includes('{{') || input.includes('}}')) {
    throw new NodeOperationError(
      context.getNode(),
      `${fieldLabel} looks like a template string ('{{ ... }}'). In n8n use an expression like: ={{ $json.source_kref }}`,
    );
  }

  // Tolerate common copy/paste formatting issues like "kref: //...".
  const normalized = input
    .replace(/\bkref:\s*\/\//gi, 'kref://')
    .replace(/\bkref:\/\//gi, 'kref://')
    .trim();

  if (!normalized.toLowerCase().startsWith('kref://')) {
    throw new NodeOperationError(
      context.getNode(),
      `${fieldLabel} must start with 'kref://'. Example: kref://project/space/item.kind?r=1`,
    );
  }

  return normalized;
};

const emitArray = (
  out: INodeExecutionData[],
  value: unknown,
  outputFormat: OutputFormat,
  returnAll: boolean,
  limit: number,
) => {
  if (!Array.isArray(value)) {
    out.push({ json: value as IDataObject });
    return;
  }

  const limited = applyReturnAllLimit(value, returnAll, limit);

  if (outputFormat === 'singleArray') {
    out.push({ json: limited as IDataObject });
    return;
  }

  for (const entry of limited as unknown[]) {
    out.push({ json: entry as IDataObject });
  }
};

export class KumihoAction implements INodeType {
  description: INodeTypeDescription = {
    displayName: 'Kumiho Action',
    name: 'kumihoAction',
    icon: 'file:../images/Action.svg',
    usableAsTool: true,
    group: ['transform'],
    version: 1,
    description: 'Create, read, update, or delete Kumiho resources.',
    defaults: {
      name: 'Kumiho Action',
    },
    inputs: ['main'],
    outputs: ['main'],
    credentials: [
      {
        name: 'kumihoApi',
        required: true,
      },
    ],
    properties: [
      {
        displayName: 'Resource',
        name: 'resource',
        type: 'options',
        noDataExpression: true,
        options: [
          { name: 'Artifact', value: 'artifact' },
          { name: 'Bundle', value: 'bundle' },
          { name: 'Item', value: 'item' },
          { name: 'Kref', value: 'kref' },
          { name: 'Project', value: 'project' },
          { name: 'Revision', value: 'revision' },
          { name: 'Space', value: 'space' },
        ],
        default: 'project',
      },
      {
        displayName: 'Operation',
        name: 'operation',
        type: 'options',
        noDataExpression: true,
        options: [
          { name: 'Create', value: 'create' },
          { name: 'Read', value: 'read' },
          { name: 'Update', value: 'update' },
          { name: 'Delete', value: 'delete' },
        ],
        default: 'read',
      },

      // --- Create mode (only used where needed)
      {
        displayName: 'Create Mode',
        name: 'createModeRevision',
        type: 'options',
        options: [
          { name: 'Create Revision', value: 'revisionCreate' },
          { name: 'Create Edge', value: 'graphCreateEdge' },
        ],
        default: 'revisionCreate',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['create'],
          },
        },
      },
      {
        displayName: 'Create Mode',
        name: 'createMode',
        type: 'options',
        options: [{ name: 'Create Edge', value: 'graphCreateEdge' }],
        default: 'graphCreateEdge',
        displayOptions: {
          show: {
            resource: ['graph'],
            operation: ['create'],
          },
        },
      },

      // --- Read mode
      {
        displayName: 'Read Mode',
        name: 'readModeProject',
        type: 'options',
        options: [
          { name: 'Get Project', value: 'projectGet' },
          { name: 'List Projects', value: 'projectList' },
        ],
        default: 'projectGet',
        displayOptions: {
          show: {
            resource: ['project'],
            operation: ['read'],
          },
        },
      },
      {
        displayName: 'Read Mode',
        name: 'readModeSpace',
        type: 'options',
        options: [
          { name: 'Get Space (by Path)', value: 'spaceGet' },
          { name: 'List Spaces', value: 'spaceList' },
        ],
        default: 'spaceGet',
        displayOptions: {
          show: {
            resource: ['space'],
            operation: ['read'],
          },
        },
      },
      {
        displayName: 'Read Mode',
        name: 'readModeItem',
        type: 'options',
        options: [
          { name: 'Get Item (by Kref)', value: 'itemGet' },
          { name: 'Get Item (by Name / Kind)', value: 'itemGetByPath' },
          { name: 'Search Items', value: 'itemSearch' },
        ],
        default: 'itemGet',
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['read'],
          },
        },
      },
      {
        displayName: 'Read Mode',
        name: 'readModeRevision',
        type: 'options',
        options: [
          { name: 'Analyze Impact', value: 'graphAnalyzeImpact' },
          { name: 'Find Path', value: 'graphFindPath' },
          { name: 'Get Dependencies', value: 'graphGetDependencies' },
          { name: 'Get Revision (as of Timestamp)', value: 'revisionGetAsOf' },
          { name: 'Get Revision (by Kref)', value: 'revisionGetByKref' },
          { name: 'Get Revision (by Tag)', value: 'revisionGetByTag' },
          { name: 'Has Tag', value: 'revisionHasTag' },
          { name: 'List Edges', value: 'graphListEdges' },
          { name: 'List Revision Artifacts', value: 'revisionListArtifacts' },
          { name: 'List Revision Tags', value: 'revisionListTags' },
          { name: 'Was Tagged', value: 'revisionWasTagged' },
        ],
        default: 'revisionGetByKref',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
          },
        },
      },
      {
        displayName: 'Read Mode',
        name: 'readModeArtifact',
        type: 'options',
        options: [
          { name: 'Get Artifact (by Revision Kref + Name)', value: 'artifactGet' },
          { name: 'Get Artifacts (by Location)', value: 'artifactGetByLocation' },
        ],
        default: 'artifactGet',
        displayOptions: {
          show: {
            resource: ['artifact'],
            operation: ['read'],
          },
        },
      },
      {
        displayName: 'Read Mode',
        name: 'readModeBundle',
        type: 'options',
        options: [
          { name: 'Get Bundle', value: 'bundleGet' },
          { name: 'List Bundle Members', value: 'bundleListMembers' },
          { name: 'Bundle History', value: 'bundleHistory' },
        ],
        default: 'bundleGet',
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['read'],
          },
        },
      },
      {
        displayName: 'Read Mode',
        name: 'readModeGraph',
        type: 'options',
        options: [
          { name: 'List Edges', value: 'graphListEdges' },
          { name: 'Get Dependencies', value: 'graphGetDependencies' },
          { name: 'Find Path', value: 'graphFindPath' },
          { name: 'Analyze Impact', value: 'graphAnalyzeImpact' },
        ],
        default: 'graphListEdges',
        displayOptions: {
          show: {
            resource: ['graph'],
            operation: ['read'],
          },
        },
      },
      {
        displayName: 'Read Mode',
        name: 'readModeKref',
        type: 'options',
        options: [{ name: 'Resolve', value: 'krefResolve' }],
        default: 'krefResolve',
        displayOptions: {
          show: {
            resource: ['kref'],
            operation: ['read'],
          },
        },
      },

      // --- Update mode
      {
        displayName: 'Update Mode',
        name: 'updateModeItem',
        type: 'options',
        options: [
          { name: 'Update Metadata', value: 'itemUpdateMetadata' },
          { name: 'Set Attribute', value: 'itemSetAttribute' },
          { name: 'Deprecate', value: 'itemDeprecate' },
        ],
        default: 'itemUpdateMetadata',
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['update'],
          },
        },
      },
      {
        displayName: 'Update Mode',
        name: 'updateModeRevision',
        type: 'options',
        options: [
          { name: 'Update Metadata', value: 'revisionUpdateMetadata' },
          { name: 'Deprecate', value: 'revisionDeprecate' },
          { name: 'Set Tag', value: 'revisionSetTag' },
          { name: 'Remove Tag', value: 'revisionRemoveTag' },
        ],
        default: 'revisionUpdateMetadata',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['update'],
          },
        },
      },
      {
        displayName: 'Update Mode',
        name: 'updateModeArtifact',
        type: 'options',
        options: [
          { name: 'Update Metadata', value: 'artifactUpdateMetadata' },
          { name: 'Deprecate', value: 'artifactDeprecate' },
        ],
        default: 'artifactUpdateMetadata',
        displayOptions: {
          show: {
            resource: ['artifact'],
            operation: ['update'],
          },
        },
      },
      {
        displayName: 'Update Mode',
        name: 'updateModeBundle',
        type: 'options',
        options: [
          { name: 'Add Member', value: 'bundleAddMember' },
          { name: 'Remove Member', value: 'bundleRemoveMember' },
        ],
        default: 'bundleAddMember',
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['update'],
          },
        },
      },

      // --- Common inputs
      {
        displayName: 'Project Name',
        name: 'projectName',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['project'],
            operation: ['create', 'read', 'delete'],
          },
        },
      },
      {
        displayName: 'Description',
        name: 'projectDescription',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['project'],
            operation: ['create'],
          },
        },
      },
      {
        displayName: 'Force Delete',
        name: 'force',
        type: 'boolean',
        default: false,
        description: 'Whether to permanently delete and remove all contents',
        displayOptions: {
          show: {
            resource: ['project', 'space', 'item', 'bundle', 'revision', 'artifact'],
            operation: ['delete'],
          },
        },
      },

      // Space fields
      {
        displayName: 'Space Path',
        name: 'spacePath',
        type: 'string',
        default: '',
        description: 'Example: project/space',
        displayOptions: {
          show: {
            resource: ['space'],
            operation: ['read', 'delete'],
          },
        },
      },
      {
        displayName: 'Parent Path',
        name: 'parentPath',
        type: 'string',
        default: '',
        description: 'Required for Space Create and Space List. Example: project or project/parent-space.',
        displayOptions: {
          show: {
            resource: ['space'],
            operation: ['create', 'read'],
          },
        },
      },
      {
        displayName: 'Space Name',
        name: 'spaceName',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['space'],
            operation: ['create'],
          },
        },
      },
      {
        displayName: 'Recursive',
        name: 'recursive',
        type: 'boolean',
        default: false,
        displayOptions: {
          show: {
            resource: ['space'],
            operation: ['read'],
            readModeSpace: ['spaceList'],
          },
        },
      },

      // Item fields
      {
        displayName: 'Item Kref',
        name: 'itemKrefRevisionCreate',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['create'],
            createModeRevision: ['revisionCreate'],
          },
        },
      },
      {
        displayName: 'Item Kref',
        name: 'itemKrefRevisionGetByTag',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['revisionGetByTag'],
          },
        },
      },
      {
        displayName: 'Item Kref',
        name: 'itemKrefRevisionGetAsOf',
        type: 'string',
        default: '',
        description: 'Item reference (e.g., kref://project/space/item.kind)',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['revisionGetAsOf'],
          },
        },
      },
      {
        displayName: 'Tag',
        name: 'tagAsOf',
        type: 'string',
        default: 'published',
        description: 'Tag to query (e.g., published, approved, latest)',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['revisionGetAsOf'],
          },
        },
      },
      {
        displayName: 'Timestamp',
        name: 'timestampAsOf',
        type: 'string',
        default: '',
        description: 'Point in time to query. Supports YYYYMMDDHHMM format (e.g., 202506011430) or ISO 8601 format (e.g., 2025-06-01T14:30:00Z)',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['revisionGetAsOf'],
          },
        },
      },
      {
        displayName: 'Item Kref',
        name: 'itemKrefRevisionHasTag',
        type: 'string',
        default: '',
        description: 'Example: kref://project/space/item.kind',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['revisionHasTag'],
          },
        },
      },
      {
        displayName: 'Revision Kref',
        name: 'revisionKrefReadGetByKref',
        type: 'string',
        default: '',
        description: 'Example: kref://project/space/item.kind?r=1',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['revisionGetByKref'],
          },
        },
      },
      {
        displayName: 'Kref',
        name: 'kref',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['revision', 'kref'],
            operation: ['read'],
          },
          hide: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: [
              'revisionGetByKref',
              'revisionGetByTag',
              'revisionGetAsOf',
              'revisionHasTag',
              'revisionListArtifacts',
              'graphListEdges',
              'graphGetDependencies',
              'graphFindPath',
              'graphAnalyzeImpact',
            ],
          },
        },
      },
      {
        displayName: 'Kref',
        name: 'krefResolveKref',
        type: 'string',
        default: '',
        description: 'Kref to resolve (e.g., kref://project/space/item.kind, kref://.../item.kind?r=1, or kref://.../item.kind?r=1&a=mesh.fbx)',
        displayOptions: {
          show: {
            resource: ['kref'],
            operation: ['read'],
            readModeKref: ['krefResolve'],
          },
        },
      },
      {
        displayName: 'Kref',
        name: 'krefRevisionUpdate',
        type: 'string',
        default: '',
        description: 'Example: kref://project/space/item.kind?r=1',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['update'],
            updateModeRevision: ['__legacy'],
          },
        },
      },
      {
        displayName: 'Item Kref',
        name: 'itemKrefItem',
        type: 'string',
        default: '',
        description: 'Example: kref://project/space/item.kind',
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['read'],
            readModeItem: ['itemGet'],
          },
        },
      },
      {
        displayName: 'Item Kref',
        name: 'itemKrefItem',
        type: 'string',
        default: '',
        description: 'Example: kref://project/space/item.kind',
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['update', 'delete'],
          },
        },
      },

      // Item metadata (shown only where used)
      {
        displayName: 'Metadata',
        name: 'itemMetadataCreate',
        type: 'json',
        default: '{}',
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['create'],
          },
        },
      },
      {
        displayName: 'Metadata',
        name: 'itemMetadataUpdate',
        type: 'json',
        default: '{}',
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['update'],
            updateModeItem: ['itemUpdateMetadata'],
          },
        },
      },
      {
        displayName: 'Space Path',
        name: 'itemSpacePath',
        type: 'string',
        default: '',
        description: 'Example: /project/space',
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['create'],
          },
        },
      },
      {
        displayName: 'Space Path',
        name: 'itemSpacePath',
        type: 'string',
        default: '',
        description: 'Example: /project/space',
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['read'],
            readModeItem: ['itemGetByPath'],
          },
        },
      },
      {
        displayName: 'Item Name',
        name: 'itemName',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['create'],
          },
        },
      },
      {
        displayName: 'Item Name',
        name: 'itemName',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['read'],
            readModeItem: ['itemGetByPath'],
          },
        },
      },
      {
        displayName: 'Kind',
        name: 'itemKind',
        type: 'string',
        default: 'model',
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['create'],
          },
        },
      },
      {
        displayName: 'Kind',
        name: 'itemKind',
        type: 'string',
        default: 'model',
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['read'],
            readModeItem: ['itemGetByPath'],
          },
        },
      },
      {
        displayName: 'Context Filter',
        name: 'contextFilter',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            operation: ['read'],
            readModeItem: ['itemSearch'],
          },
        },
      },
      {
        displayName: 'Name Filter',
        name: 'nameFilter',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            operation: ['read'],
            readModeItem: ['itemSearch'],
          },
        },
      },
      {
        displayName: 'Kind Filter',
        name: 'kindFilter',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            operation: ['read'],
            readModeItem: ['itemSearch'],
          },
        },
      },
      {
        displayName: 'Attribute Key',
        name: 'attributeKey',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            operation: ['update'],
            updateModeItem: ['itemSetAttribute'],
          },
        },
      },
      {
        displayName: 'Attribute Value',
        name: 'attributeValue',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            operation: ['update'],
            updateModeItem: ['itemSetAttribute'],
          },
        },
      },
      {
        displayName: 'Deprecated',
        name: 'deprecated',
        type: 'boolean',
        default: true,
        displayOptions: {
          show: {
            operation: ['update'],
            updateModeItem: ['itemDeprecate'],
          },
        },
      },
      {
        displayName: 'Deprecated',
        name: 'deprecatedRevision',
        type: 'boolean',
        default: true,
        displayOptions: {
          show: {
            operation: ['update'],
            updateModeRevision: ['revisionDeprecate'],
          },
        },
      },
      {
        displayName: 'Deprecated',
        name: 'deprecatedArtifact',
        type: 'boolean',
        default: true,
        displayOptions: {
          show: {
            operation: ['update'],
            updateModeArtifact: ['artifactDeprecate'],
          },
        },
      },

      // Revision fields
      {
        displayName: 'Revision Number',
        name: 'revisionNumberCreate',
        type: 'string',
        default: '',
        description: 'Optional. Non-negative integer.',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['create'],
            createModeRevision: ['revisionCreate'],
          },
        },
      },
      {
        displayName: 'Revision Number',
        name: 'revisionNumberRead',
        type: 'string',
        default: '',
        description: 'Optional. Non-negative integer.',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['revisionListArtifacts', 'revisionListTags', 'revisionWasTagged'],
          },
        },
      },
      {
        displayName: 'Revision Number',
        name: 'revisionNumberUpdateDelete',
        type: 'string',
        default: '',
        description: 'Optional. Non-negative integer.',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['update'],
            updateModeRevision: ['__legacy'],
          },
        },
      },
      {
        displayName: 'Tag',
        name: 'tagRead',
        type: 'string',
        default: 'latest',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['revisionGetByTag', 'revisionHasTag', 'revisionWasTagged'],
          },
        },
      },
      {
        displayName: 'Revision Kref',
        name: 'revisionKrefUpdateMetadata',
        type: 'string',
        default: '',
        description: 'Example: kref://project/space/item.kind?r=1',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['update'],
            updateModeRevision: ['revisionUpdateMetadata'],
          },
        },
      },
      {
        displayName: 'Metadata',
        name: 'revisionMetadataUpdate',
        type: 'json',
        default: '{}',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['update'],
            updateModeRevision: ['revisionUpdateMetadata'],
          },
        },
      },
      {
        displayName: 'Revision Kref',
        name: 'revisionKrefTagUpdate',
        type: 'string',
        default: '',
        description: 'Example: kref://project/space/item.kind?r=1',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['update'],
            updateModeRevision: ['revisionSetTag', 'revisionRemoveTag'],
          },
        },
      },
      {
        displayName: 'Revision Tag',
        name: 'tagUpdate',
        type: 'string',
        default: 'latest',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['update'],
            updateModeRevision: ['revisionSetTag', 'revisionRemoveTag'],
          },
        },
      },

      {
        displayName: 'Revision Kref',
        name: 'revisionKrefDeprecate',
        type: 'string',
        default: '',
        description: 'Example: kref://project/space/item.kind?r=1',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['update'],
            updateModeRevision: ['revisionDeprecate'],
          },
        },
      },
      {
        displayName: 'Revision Kref',
        name: 'revisionKrefDelete',
        type: 'string',
        default: '',
        description: 'Example: kref://project/space/item.kind?r=1',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['delete'],
          },
        },
      },

      // Artifact fields
      {
        displayName: 'Revision Kref',
        name: 'revisionKrefRevision',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['revisionListArtifacts'],
          },
        },
      },
      {
        displayName: 'Revision Kref',
        name: 'revisionKrefArtifact',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['artifact'],
            operation: ['read'],
            readModeArtifact: ['artifactGet'],
          },
        },
      },
      {
        displayName: 'Revision Kref',
        name: 'revisionKrefGraph',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['graphListEdges', 'graphGetDependencies', 'graphAnalyzeImpact'],
          },
        },
      },
      {
        displayName: 'Artifact Name',
        name: 'artifactName',
        type: 'string',
        default: '',
        description: 'Optional. If empty, the revision\'s default artifact will be used.',
        displayOptions: {
          show: {
            resource: ['artifact', 'kref'],
            operation: ['read'],
            readModeArtifact: ['artifactGet'],
          },
        },
      },
      {
        displayName: 'Location',
        name: 'locationQuery',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['artifact'],
            operation: ['read'],
            readModeArtifact: ['artifactGetByLocation'],
          },
        },
      },

      // Bundle fields
      {
        displayName: 'Bundle Kref',
        name: 'bundleKref',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['update'],
          },
        },
      },
      {
        displayName: 'Bundle Kref',
        name: 'bundleKref',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['read'],
            readModeBundle: ['bundleListMembers', 'bundleHistory'],
          },
        },
      },
      {
        displayName: 'Bundle Kref',
        name: 'bundleKrefDelete',
        type: 'string',
        default: '',
        description: 'Example: kref://project/space/bundle.bundle',
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['delete'],
          },
        },
      },
      {
        displayName: 'Bundle Name',
        name: 'bundleName',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['create'],
          },
        },
      },
      {
        displayName: 'Bundle Name',
        name: 'bundleName',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['read'],
            readModeBundle: ['bundleGet'],
          },
        },
      },
      {
        displayName: 'Space Path',
        name: 'bundleSpacePath',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['create'],
          },
        },
      },
      {
        displayName: 'Space Path',
        name: 'bundleSpacePath',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['read'],
            readModeBundle: ['bundleGet'],
          },
        },
      },
      {
        displayName: 'Advanced: Bundle Kref Override',
        name: 'bundleKrefOverrideEnabled',
        type: 'boolean',
        default: false,
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['read'],
            readModeBundle: ['bundleGet'],
          },
        },
      },
      {
        displayName: 'Bundle Kref (Override)',
        name: 'bundleKrefOverride',
        type: 'string',
        default: '',
        description: 'Optional. When set, this Kref is used instead of computing it from Space Path + Bundle Name.',
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['read'],
            readModeBundle: ['bundleGet'],
            bundleKrefOverrideEnabled: [true],
          },
        },
      },
      {
        displayName: 'Item Kref(s)',
        name: 'bundleItemKref',
        type: 'string',
        default: '',
        description: 'Single kref or a list (one per line, or comma-separated). Example: kref://project/space/item.kind.',
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['update'],
            updateModeBundle: ['bundleAddMember', 'bundleRemoveMember'],
          },
        },
      },
      {
        displayName: 'Member Revision Number',
        name: 'bundleRevisionNumber',
        type: 'string',
        default: '',
        description: 'Optional. Non-negative integer.',
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['read'],
            readModeBundle: ['bundleListMembers'],
          },
        },
      },

      // Graph fields
      {
        displayName: 'Source Kref',
        name: 'sourceKref',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['create', 'read'],
            createModeRevision: ['graphCreateEdge'],
          },
        },
      },
      {
        displayName: 'Target Kref',
        name: 'targetKref',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['create', 'read'],
            createModeRevision: ['graphCreateEdge'],
          },
        },
      },
      {
        displayName: 'Edge Type',
        name: 'edgeType',
        type: 'options',
        options: [
          { name: 'Belongs To', value: 'BELONGS_TO' },
          { name: 'Contains', value: 'CONTAINS' },
          { name: 'Created From', value: 'CREATED_FROM' },
          { name: 'Depends On', value: 'DEPENDS_ON' },
          { name: 'Derived From', value: 'DERIVED_FROM' },
          { name: 'Referenced', value: 'REFERENCED' },
        ],
        default: 'DEPENDS_ON',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['create'],
            createModeRevision: ['graphCreateEdge'],
          },
        },
      },
      {
        displayName: 'Edge Type Filter',
        name: 'edgeTypeFilter',
        type: 'string',
        default: '',
        description: 'Optional. Single edge type.',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['graphListEdges'],
          },
        },
      },
      {
        displayName: 'Direction',
        name: 'direction',
        type: 'options',
        options: [
          { name: 'Outgoing', value: 0 },
          { name: 'Incoming', value: 1 },
          { name: 'Both', value: 2 },
        ],
        default: 0,
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['graphListEdges'],
          },
        },
      },
      {
        displayName: 'Max Depth',
        name: 'maxDepth',
        type: 'number',
        default: 5,
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['graphGetDependencies', 'graphFindPath', 'graphAnalyzeImpact'],
          },
        },
      },
      {
        displayName: 'Edge Types',
        name: 'edgeTypes',
        type: 'string',
        default: '',
        description: 'Optional. Comma-separated list (e.g., DEPENDS_ON,DERIVED_FROM).',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['graphGetDependencies', 'graphFindPath', 'graphAnalyzeImpact'],
          },
        },
      },

      // Artifact create fields (order matters for UX)
      {
        displayName: 'Revision Kref',
        name: 'artifactRevisionKref',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['artifact'],
            operation: ['create'],
          },
        },
      },
      {
        displayName: 'Name',
        name: 'name',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['artifact'],
            operation: ['create'],
          },
        },
      },
      {
        displayName: 'Location',
        name: 'locationCreate',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['artifact'],
            operation: ['create'],
          },
        },
      },

      // Artifact metadata (shown only where used)
      {
        displayName: 'Metadata',
        name: 'artifactMetadataCreate',
        type: 'json',
        default: '{}',
        displayOptions: {
          show: {
            resource: ['artifact'],
            operation: ['create'],
          },
        },
      },

      // Shared JSON metadata
      {
        displayName: 'Metadata',
        name: 'metadata',
        type: 'json',
        default: {},
        displayOptions: {
          show: {
            operation: ['create', 'update'],
            resource: ['revision', 'bundle', 'graph'],
          },
          hide: {
            resource: ['revision'],
            operation: ['update'],
            updateModeRevision: ['revisionUpdateMetadata', 'revisionSetTag', 'revisionRemoveTag', 'revisionDeprecate'],
          },
        },
      },

      // Artifact fields
      {
        displayName: 'Artifact Kref',
        name: 'artifactKref',
        type: 'string',
        default: '',
        displayOptions: {
          show: {
            resource: ['artifact'],
            operation: ['update', 'delete'],
          },
        },
      },

      {
        displayName: 'Metadata',
        name: 'artifactMetadataUpdate',
        type: 'json',
        default: '{}',
        displayOptions: {
          show: {
            resource: ['artifact'],
            operation: ['update'],
            updateModeArtifact: ['artifactUpdateMetadata'],
          },
        },
      },

      // Kref resolve optional params
      {
        displayName: 'Resolve Revision Number',
        name: 'krefResolveRevisionNumber',
        type: 'string',
        default: '',
        description: 'Optional. Non-negative integer (maps to r).',
        displayOptions: {
          show: {
            resource: ['kref'],
            operation: ['read'],
            readModeKref: ['krefResolve'],
          },
        },
      },
      {
        displayName: 'Resolve Tag',
        name: 'krefResolveTag',
        type: 'string',
        default: '',
        description: 'Optional (maps to t)',
        displayOptions: {
          show: {
            resource: ['kref'],
            operation: ['read'],
            readModeKref: ['krefResolve'],
          },
        },
      },
      {
        displayName: 'Resolve Artifact Name',
        name: 'krefResolveArtifactName',
        type: 'string',
        default: '',
        description: 'Optional (maps to a)',
        displayOptions: {
          show: {
            resource: ['kref'],
            operation: ['read'],
            readModeKref: ['krefResolve'],
          },
        },
      },

      // --- Advanced output shaping (only for list-like reads)
      {
        displayName: 'Output Format',
        name: 'outputFormatProject',
        type: 'options',
        options: [
          { name: 'Split Into Items', value: 'split' },
          { name: 'Single Item (Array)', value: 'singleArray' },
        ],
        default: 'split',
        displayOptions: {
          show: {
            resource: ['project'],
            operation: ['read'],
            readModeProject: ['projectList'],
          },
        },
      },
      {
        displayName: 'Return All',
        name: 'returnAllProject',
        type: 'boolean',
        default: true,
        description: 'Whether to return all results or limit the result size',
        displayOptions: {
          show: {
            resource: ['project'],
            operation: ['read'],
            readModeProject: ['projectList'],
          },
        },
      },
      {
        displayName: 'Limit',
        name: 'limitProject',
        type: 'number',
        default: 100,
        typeOptions: { minValue: 1 },
        displayOptions: {
          show: {
            resource: ['project'],
            operation: ['read'],
            readModeProject: ['projectList'],
            returnAllProject: [false],
          },
        },
      },
      {
        displayName: 'Output Format',
        name: 'outputFormatSpace',
        type: 'options',
        options: [
          { name: 'Split Into Items', value: 'split' },
          { name: 'Single Item (Array)', value: 'singleArray' },
        ],
        default: 'split',
        displayOptions: {
          show: {
            resource: ['space'],
            operation: ['read'],
            readModeSpace: ['spaceList'],
          },
        },
      },
      {
        displayName: 'Return All',
        name: 'returnAllSpace',
        type: 'boolean',
        default: true,
        displayOptions: {
          show: {
            resource: ['space'],
            operation: ['read'],
            readModeSpace: ['spaceList'],
          },
        },
      },
      {
        displayName: 'Limit',
        name: 'limitSpace',
        type: 'number',
        default: 100,
        typeOptions: { minValue: 1 },
        displayOptions: {
          show: {
            resource: ['space'],
            operation: ['read'],
            readModeSpace: ['spaceList'],
            returnAllSpace: [false],
          },
        },
      },
      {
        displayName: 'Output Format',
        name: 'outputFormatItem',
        type: 'options',
        options: [
          { name: 'Split Into Items', value: 'split' },
          { name: 'Single Item (Array)', value: 'singleArray' },
        ],
        default: 'split',
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['read'],
            readModeItem: ['itemSearch'],
          },
        },
      },
      {
        displayName: 'Return All',
        name: 'returnAllItem',
        type: 'boolean',
        default: true,
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['read'],
            readModeItem: ['itemSearch'],
          },
        },
      },
      {
        displayName: 'Limit',
        name: 'limitItem',
        type: 'number',
        default: 100,
        typeOptions: { minValue: 1 },
        displayOptions: {
          show: {
            resource: ['item'],
            operation: ['read'],
            readModeItem: ['itemSearch'],
            returnAllItem: [false],
          },
        },
      },
      {
        displayName: 'Output Format',
        name: 'outputFormatRevision',
        type: 'options',
        options: [
          { name: 'Split Into Items', value: 'split' },
          { name: 'Single Item (Array)', value: 'singleArray' },
        ],
        default: 'split',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['revisionListArtifacts'],
          },
        },
      },
      {
        displayName: 'Return All',
        name: 'returnAllRevision',
        type: 'boolean',
        default: true,
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['revisionListArtifacts'],
          },
        },
      },
      {
        displayName: 'Limit',
        name: 'limitRevision',
        type: 'number',
        default: 100,
        typeOptions: { minValue: 1 },
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['revisionListArtifacts'],
            returnAllRevision: [false],
          },
        },
      },
      {
        displayName: 'Output Format',
        name: 'outputFormatArtifact',
        type: 'options',
        options: [
          { name: 'Split Into Items', value: 'split' },
          { name: 'Single Item (Array)', value: 'singleArray' },
        ],
        default: 'split',
        displayOptions: {
          show: {
            resource: ['artifact'],
            operation: ['read'],
            readModeArtifact: ['artifactGetByLocation'],
          },
        },
      },
      {
        displayName: 'Return All',
        name: 'returnAllArtifact',
        type: 'boolean',
        default: true,
        displayOptions: {
          show: {
            resource: ['artifact'],
            operation: ['read'],
            readModeArtifact: ['artifactGetByLocation'],
          },
        },
      },
      {
        displayName: 'Limit',
        name: 'limitArtifact',
        type: 'number',
        default: 100,
        typeOptions: { minValue: 1 },
        displayOptions: {
          show: {
            resource: ['artifact'],
            operation: ['read'],
            readModeArtifact: ['artifactGetByLocation'],
            returnAllArtifact: [false],
          },
        },
      },
      {
        displayName: 'Output Format',
        name: 'outputFormatBundle',
        type: 'options',
        options: [
          { name: 'Split Into Items', value: 'split' },
          { name: 'Single Item (Array)', value: 'singleArray' },
        ],
        default: 'split',
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['read'],
            readModeBundle: ['bundleListMembers', 'bundleHistory'],
          },
        },
      },
      {
        displayName: 'Return All',
        name: 'returnAllBundle',
        type: 'boolean',
        default: true,
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['read'],
            readModeBundle: ['bundleListMembers', 'bundleHistory'],
          },
        },
      },
      {
        displayName: 'Limit',
        name: 'limitBundle',
        type: 'number',
        default: 100,
        typeOptions: { minValue: 1 },
        displayOptions: {
          show: {
            resource: ['bundle'],
            operation: ['read'],
            readModeBundle: ['bundleListMembers', 'bundleHistory'],
            returnAllBundle: [false],
          },
        },
      },
      {
        displayName: 'Output Format',
        name: 'outputFormatGraph',
        type: 'options',
        options: [
          { name: 'Split Into Items', value: 'split' },
          { name: 'Single Item (Array)', value: 'singleArray' },
        ],
        default: 'split',
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['graphListEdges'],
          },
        },
      },
      {
        displayName: 'Return All',
        name: 'returnAllGraph',
        type: 'boolean',
        default: true,
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['graphListEdges'],
          },
        },
      },
      {
        displayName: 'Limit',
        name: 'limitGraph',
        type: 'number',
        default: 100,
        typeOptions: { minValue: 1 },
        displayOptions: {
          show: {
            resource: ['revision'],
            operation: ['read'],
            readModeRevision: ['graphListEdges'],
            returnAllGraph: [false],
          },
        },
      },
    ],
  };

  async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
    const items = this.getInputData();
    const out: INodeExecutionData[] = [];

    for (let index = 0; index < items.length; index += 1) {
      const resource = this.getNodeParameter('resource', index) as Resource;
      const operation = this.getNodeParameter('operation', index) as CrudOperation;

      // Default for list-like reads (some resources expose output shaping)
      let outputFormat: OutputFormat = 'split';
      let returnAll = true;
      let limit = 100;

      if (operation === 'read') {
        if (resource === 'project') {
          const readMode = this.getNodeParameter('readModeProject', index) as ReadModeProject;
          if (readMode === 'projectList') {
            outputFormat = this.getNodeParameter('outputFormatProject', index, 'split') as OutputFormat;
            returnAll = this.getNodeParameter('returnAllProject', index, true) as boolean;
            limit = this.getNodeParameter('limitProject', index, 100) as number;
          }
        }

        if (resource === 'space') {
          const readMode = this.getNodeParameter('readModeSpace', index) as ReadModeSpace;
          if (readMode === 'spaceList') {
            outputFormat = this.getNodeParameter('outputFormatSpace', index, 'split') as OutputFormat;
            returnAll = this.getNodeParameter('returnAllSpace', index, true) as boolean;
            limit = this.getNodeParameter('limitSpace', index, 100) as number;
          }
        }

        if (resource === 'item') {
          const readMode = this.getNodeParameter('readModeItem', index) as ReadModeItem;
          if (readMode === 'itemSearch') {
            outputFormat = this.getNodeParameter('outputFormatItem', index, 'split') as OutputFormat;
            returnAll = this.getNodeParameter('returnAllItem', index, true) as boolean;
            limit = this.getNodeParameter('limitItem', index, 100) as number;
          }
        }

        if (resource === 'revision') {
          const readMode = this.getNodeParameter('readModeRevision', index) as ReadModeRevision;
          if (readMode === 'revisionListArtifacts') {
            outputFormat = this.getNodeParameter('outputFormatRevision', index, 'split') as OutputFormat;
            returnAll = this.getNodeParameter('returnAllRevision', index, true) as boolean;
            limit = this.getNodeParameter('limitRevision', index, 100) as number;
          }

          if (readMode === 'graphListEdges') {
            outputFormat = this.getNodeParameter('outputFormatGraph', index, 'split') as OutputFormat;
            returnAll = this.getNodeParameter('returnAllGraph', index, true) as boolean;
            limit = this.getNodeParameter('limitGraph', index, 100) as number;
          }
        }

        if (resource === 'artifact') {
          const readMode = this.getNodeParameter('readModeArtifact', index) as ReadModeArtifact;
          if (readMode === 'artifactGetByLocation') {
            outputFormat = this.getNodeParameter('outputFormatArtifact', index, 'split') as OutputFormat;
            returnAll = this.getNodeParameter('returnAllArtifact', index, true) as boolean;
            limit = this.getNodeParameter('limitArtifact', index, 100) as number;
          }
        }

        if (resource === 'bundle') {
          const readMode = this.getNodeParameter('readModeBundle', index) as ReadModeBundle;
          if (readMode === 'bundleListMembers' || readMode === 'bundleHistory') {
            outputFormat = this.getNodeParameter('outputFormatBundle', index, 'split') as OutputFormat;
            returnAll = this.getNodeParameter('returnAllBundle', index, true) as boolean;
            limit = this.getNodeParameter('limitBundle', index, 100) as number;
          }
        }

        if (resource === 'graph') {
          const readMode = this.getNodeParameter('readModeGraph', index) as ReadModeGraph;
          if (readMode === 'graphListEdges') {
            outputFormat = this.getNodeParameter('outputFormatGraph', index, 'split') as OutputFormat;
            returnAll = this.getNodeParameter('returnAllGraph', index, true) as boolean;
            limit = this.getNodeParameter('limitGraph', index, 100) as number;
          }
        }
      }

      if (resource === 'project') {
        if (operation === 'create') {
          const name = normalizeProjectName(this, this.getNodeParameter('projectName', index));

          const description = String(this.getNodeParameter('projectDescription', index, '') ?? '').trim();
          const body: IDataObject = { name };
          if (description) body.description = description;

          try {
            const data = await kumihoRequest(this, {
              method: 'POST',
              path: '/api/v1/projects',
              body,
            });
            out.push({ json: stripProjectMetadata(data) as IDataObject });
            continue;
          } catch (error) {
            const err = error as unknown as { httpCode?: unknown; kumiho?: { status_code?: unknown } };
            const httpCode = Number(err?.httpCode ?? err?.kumiho?.status_code);
            if ([500, 502, 503, 504].includes(httpCode)) {
              try {
                const existing = await kumihoRequest(this, {
                  method: 'GET',
                  path: `/api/v1/projects/${encodeURIComponent(name)}`,
                  maxAttempts: 1,
                  timeoutMs: 15000,
                  retryBudgetMs: 15000,
                });
                out.push({ json: stripProjectMetadata(existing) as IDataObject });
                continue;
              } catch {
                // fall through and throw original error
              }
            }
            throw error;
          }
        }

        if (operation === 'read') {
          const readMode = this.getNodeParameter('readModeProject', index) as ReadModeProject;
          if (readMode === 'projectGet') {
            const name = normalizeProjectName(this, this.getNodeParameter('projectName', index));
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: `/api/v1/projects/${encodeURIComponent(name)}`,
            });
              out.push({ json: stripProjectMetadata(data) as IDataObject });
            continue;
          }

          if (readMode === 'projectList') {
            const data = await kumihoRequest(this, { method: 'GET', path: '/api/v1/projects' });
              emitArray(out, stripProjectMetadata(data), outputFormat, returnAll, limit);
            continue;
          }
        }

        if (operation === 'delete') {
          const name = normalizeProjectName(this, this.getNodeParameter('projectName', index));
          const force = this.getNodeParameter('force', index, false) as boolean;
          const data = await kumihoRequest(this, {
            method: 'DELETE',
            path: `/api/v1/projects/${encodeURIComponent(name)}`,
            qs: { force },
          });
          out.push({ json: data as IDataObject });
          continue;
        }
      }

      if (resource === 'space') {
        if (operation === 'create') {
          const parentPath = normalizeKumihoPath(this, this.getNodeParameter('parentPath', index), 'Parent Path');
          const name = String(this.getNodeParameter('spaceName', index) ?? '').trim();
          if (!parentPath) {
            throw new NodeOperationError(this.getNode(), 'Parent Path is required');
          }
          if (!name) {
            throw new NodeOperationError(this.getNode(), 'Space Name is required');
          }

          try {
            const data = await kumihoRequest(this, {
              method: 'POST',
              path: '/api/v1/spaces',
              body: { parent_path: parentPath, name },
            });
            out.push({ json: data as IDataObject });
            continue;
          } catch (error) {
            const err = error as { httpCode?: unknown; kumiho?: { status_code?: unknown } };
            const httpCode = Number(err?.httpCode ?? err?.kumiho?.status_code);
            if ([500, 502, 503, 504].includes(httpCode)) {
              try {
                const fullPath = `${parentPath}/${name}`;
                const existing = await kumihoRequest(this, {
                  method: 'GET',
                  path: '/api/v1/spaces/by-path',
                  qs: { path: fullPath },
                  maxAttempts: 1,
                  timeoutMs: 15000,
                  retryBudgetMs: 15000,
                });
                out.push({ json: existing as IDataObject });
                continue;
              } catch {
                // fall through and throw original error
              }
            }
            throw error;
          }
        }

        if (operation === 'read') {
          const readMode = this.getNodeParameter('readModeSpace', index) as ReadModeSpace;
          if (readMode === 'spaceGet') {
            const path = normalizeKumihoPath(this, this.getNodeParameter('spacePath', index), 'Space Path');
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/spaces/by-path',
              qs: { path },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (readMode === 'spaceList') {
            const parentPath = normalizeKumihoPath(this, this.getNodeParameter('parentPath', index), 'Parent Path');
            const recursive = this.getNodeParameter('recursive', index, false) as boolean;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/spaces',
              qs: { parent_path: parentPath, recursive },
            });
            emitArray(out, data, outputFormat, returnAll, limit);
            continue;
          }
        }

        if (operation === 'delete') {
          const path = normalizeKumihoPath(this, this.getNodeParameter('spacePath', index), 'Space Path');
          const force = this.getNodeParameter('force', index, false) as boolean;
          const data = await kumihoRequest(this, {
            method: 'DELETE',
            path: '/api/v1/spaces/by-path',
            qs: { path, force },
          });
          out.push({ json: data as IDataObject });
          continue;
        }
      }

      if (resource === 'item') {
        if (operation === 'create') {
          const spacePath = normalizeKumihoPath(this, this.getNodeParameter('itemSpacePath', index), 'Space Path');
          const itemName = this.getNodeParameter('itemName', index) as string;
          const kind = this.getNodeParameter('itemKind', index) as string;
          const metadata = normalizeMetadata(
            this.getNodeParameter('itemMetadataCreate', index, this.getNodeParameter('metadata', index, {})),
          );
          const data = await kumihoRequest(this, {
            method: 'POST',
            path: '/api/v1/items',
            body: {
              space_path: spacePath,
              item_name: itemName,
              kind,
              metadata,
            },
          });
          out.push({ json: data as IDataObject });
          continue;
        }

        if (operation === 'read') {
          const readMode = this.getNodeParameter('readModeItem', index) as ReadModeItem;
          if (readMode === 'itemGet') {
            const kref =
              (String(this.getNodeParameter('itemKrefItem', index, '') ?? '').trim() ||
                String(this.getNodeParameter('kref', index, '') ?? '').trim()) as string;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/items/by-kref',
              qs: { kref },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (readMode === 'itemGetByPath') {
            const spacePath = normalizeKumihoPath(this, this.getNodeParameter('itemSpacePath', index), 'Space Path');
            const itemName = this.getNodeParameter('itemName', index) as string;
            const kind = this.getNodeParameter('itemKind', index) as string;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/items/by-path',
              qs: { space_path: spacePath, item_name: itemName, kind },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (readMode === 'itemSearch') {
            const contextFilter = normalizeSearchContextFilter(this, this.getNodeParameter('contextFilter', index, ''));
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/items/search',
              qs: {
                context_filter: contextFilter,
                name_filter: this.getNodeParameter('nameFilter', index, '') as string,
                kind_filter: this.getNodeParameter('kindFilter', index, '') as string,
              },
            });
            emitArray(out, data, outputFormat, returnAll, limit);
            continue;
          }
        }

        if (operation === 'update') {
          const updateMode = this.getNodeParameter('updateModeItem', index) as UpdateModeItem;
          const kref =
            (String(this.getNodeParameter('itemKrefItem', index, '') ?? '').trim() ||
              String(this.getNodeParameter('kref', index, '') ?? '').trim()) as string;

          if (updateMode === 'itemUpdateMetadata') {
            const metadata = normalizeMetadata(
              this.getNodeParameter('itemMetadataUpdate', index, this.getNodeParameter('metadata', index, {})),
            );
            const data = await kumihoRequest(this, {
              method: 'PATCH',
              path: '/api/v1/items/by-kref',
              qs: { kref },
              body: { metadata },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (updateMode === 'itemSetAttribute') {
            const key = this.getNodeParameter('attributeKey', index) as string;
            const value = this.getNodeParameter('attributeValue', index) as string;
            const data = await kumihoRequest(this, {
              method: 'POST',
              path: '/api/v1/attributes',
              body: { kref, key, value },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (updateMode === 'itemDeprecate') {
            const deprecated = this.getNodeParameter('deprecated', index, true) as boolean;
            const data = await kumihoRequest(this, {
              method: 'POST',
              path: '/api/v1/items/deprecate',
              qs: { kref, deprecated },
            });
            out.push({ json: data as IDataObject });
            continue;
          }
        }

        if (operation === 'delete') {
          const kref =
            (String(this.getNodeParameter('itemKrefItem', index, '') ?? '').trim() ||
              String(this.getNodeParameter('kref', index, '') ?? '').trim()) as string;
          const force = this.getNodeParameter('force', index, false) as boolean;
          const data = await kumihoRequest(this, {
            method: 'DELETE',
            path: '/api/v1/items/by-kref',
            qs: { kref, force },
          });
          out.push({ json: data as IDataObject });
          continue;
        }
      }

      if (resource === 'revision') {
        const revisionNumber = parseOptionalInt(
          (this.getNodeParameter('revisionNumberRead', index, '') as string) ||
            (this.getNodeParameter('revisionNumberCreate', index, '') as string) ||
            (this.getNodeParameter('revisionNumberUpdateDelete', index, '') as string) ||
            (this.getNodeParameter('revisionNumber', index, '') as string),
        );

        if (operation === 'create') {
          const createMode = this.getNodeParameter(
            'createModeRevision',
            index,
            'revisionCreate',
          ) as CreateModeRevision;

          if (createMode === 'revisionCreate') {
            const itemKref =
              (String(this.getNodeParameter('itemKrefRevisionCreate', index, '') ?? '').trim() ||
                String(this.getNodeParameter('itemKref', index, '') ?? '').trim()) as string;
            const metadata = normalizeMetadata(this.getNodeParameter('metadata', index, {}));
            const data = await kumihoRequest(this, {
              method: 'POST',
              path: '/api/v1/revisions',
              body: {
                item_kref: itemKref,
                metadata,
                number: revisionNumber,
              },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (createMode === 'graphCreateEdge') {
            const sourceKref = normalizeKrefString(this, this.getNodeParameter('sourceKref', index, ''), 'Source Kref');
            const targetKref = normalizeKrefString(this, this.getNodeParameter('targetKref', index, ''), 'Target Kref');
            const edgeType = this.getNodeParameter('edgeType', index) as string;
            const metadata = normalizeMetadata(this.getNodeParameter('metadata', index, {}));
            const data = await kumihoRequest(this, {
              method: 'POST',
              path: '/api/v1/graph/edges',
              body: { source_kref: sourceKref, target_kref: targetKref, edge_type: edgeType, metadata },
            });
            out.push({ json: data as IDataObject });
            continue;
          }
        }

        if (operation === 'read') {
          const readMode = this.getNodeParameter('readModeRevision', index) as ReadModeRevision;

          if (readMode === 'revisionGetByKref') {
            const revisionKref =
              (String(this.getNodeParameter('revisionKrefReadGetByKref', index, '') ?? '').trim() ||
                String(this.getNodeParameter('kref', index, '') ?? '').trim()) as string;
            const normalizedRevisionKref = normalizeKrefString(this, revisionKref, 'Revision Kref');

            const qs: Record<string, string | number | boolean | string[] | undefined> = { kref: normalizedRevisionKref };
            if (revisionNumber !== undefined) qs.r = revisionNumber;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/revisions/by-kref',
              qs,
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (readMode === 'revisionGetByTag') {
            const itemKref =
              (String(this.getNodeParameter('itemKrefRevisionGetByTag', index, '') ?? '').trim() ||
                String(this.getNodeParameter('itemKref', index, '') ?? '').trim() ||
                String(this.getNodeParameter('kref', index, '') ?? '').trim()) as string;
            const tag =
              (String(this.getNodeParameter('tagRead', index, 'latest') ?? '').trim() ||
                String(this.getNodeParameter('tag', index, 'latest') ?? '').trim() ||
                'latest') as string;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/revisions/by-kref',
              qs: { kref: itemKref, t: tag },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (readMode === 'revisionGetAsOf') {
            const itemKref =
              (String(this.getNodeParameter('itemKrefRevisionGetAsOf', index, '') ?? '').trim() ||
                String(this.getNodeParameter('itemKref', index, '') ?? '').trim() ||
                String(this.getNodeParameter('kref', index, '') ?? '').trim()) as string;
            const normalizedItemKref = normalizeKrefString(this, itemKref, 'Item Kref');
            const tag =
              (String(this.getNodeParameter('tagAsOf', index, 'published') ?? '').trim() ||
                'published') as string;
            const timestamp = String(this.getNodeParameter('timestampAsOf', index, '') ?? '').trim();
            if (!timestamp) {
              throw new NodeOperationError(this.getNode(), 'Timestamp is required for as-of query', { itemIndex: index });
            }
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/revisions/as-of',
              qs: { item_kref: normalizedItemKref, tag, time: timestamp },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (readMode === 'revisionListArtifacts') {
            const revisionKref = this.getNodeParameter('revisionKrefRevision', index) as string;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/artifacts',
              qs: { revision_kref: revisionKref, r: revisionNumber },
            });
            emitArray(out, data, outputFormat, returnAll, limit);
            continue;
          }

          if (readMode === 'revisionListTags') {
            const kref = this.getNodeParameter('kref', index) as string;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/revisions/by-kref',
              qs: { kref, r: revisionNumber },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (readMode === 'revisionHasTag') {
            const itemKref =
              (String(this.getNodeParameter('itemKrefRevisionHasTag', index, '') ?? '').trim() ||
                String(this.getNodeParameter('itemKref', index, '') ?? '').trim() ||
                String(this.getNodeParameter('kref', index, '') ?? '').trim()) as string;
            const normalizedItemKref = normalizeKrefString(this, itemKref, 'Item Kref');
            const tag =
              (String(this.getNodeParameter('tagRead', index, '') ?? '').trim() ||
                String(this.getNodeParameter('tag', index, '') ?? '').trim()) as string;

            try {
              await kumihoRequest(this, {
                method: 'GET',
                path: '/api/v1/revisions/by-kref',
                qs: { kref: normalizedItemKref, t: tag },
              });
              out.push({ json: { has_tag: true, tag } as IDataObject });
            } catch (error) {
              const err = error as unknown as { httpCode?: unknown; kumiho?: { status_code?: unknown } };
              const httpCode = Number(err?.httpCode ?? err?.kumiho?.status_code);
              if (httpCode === 404) {
                out.push({ json: { has_tag: false, tag } as IDataObject });
              } else {
                throw error;
              }
            }
            continue;
          }

          if (readMode === 'revisionWasTagged') {
            const kref = this.getNodeParameter('kref', index) as string;
            const tag =
              (String(this.getNodeParameter('tagRead', index, '') ?? '').trim() ||
                String(this.getNodeParameter('tag', index, '') ?? '').trim()) as string;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/revisions/tags/history',
              qs: { kref, tag, r: revisionNumber },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (readMode === 'graphListEdges') {
            const revisionKref = this.getNodeParameter('revisionKrefGraph', index) as string;
            const edgeType = String(this.getNodeParameter('edgeTypeFilter', index, '') ?? '').trim();
            const direction = this.getNodeParameter('direction', index, 0) as number;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/graph/edges',
              qs: { revision_kref: revisionKref, edge_type: edgeType || undefined, direction },
            });
            emitArray(out, data, outputFormat, returnAll, limit);
            continue;
          }

          if (readMode === 'graphGetDependencies') {
            const revisionKref = this.getNodeParameter('revisionKrefGraph', index) as string;
            const maxDepth = this.getNodeParameter('maxDepth', index, 5) as number;
            const edgeTypes = splitCsv(this.getNodeParameter('edgeTypes', index, ''));
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/graph/dependencies',
              qs: { revision_kref: revisionKref, max_depth: maxDepth, edge_types: edgeTypes },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (readMode === 'graphFindPath') {
            const sourceKref = normalizeKrefString(this, this.getNodeParameter('sourceKref', index, ''), 'Source Kref');
            const targetKref = normalizeKrefString(this, this.getNodeParameter('targetKref', index, ''), 'Target Kref');
            const maxDepth = this.getNodeParameter('maxDepth', index, 5) as number;
            const edgeTypes = splitCsv(this.getNodeParameter('edgeTypes', index, ''));
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/graph/path',
              qs: { source_kref: sourceKref, target_kref: targetKref, max_depth: maxDepth, edge_types: edgeTypes },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (readMode === 'graphAnalyzeImpact') {
            const revisionKref = this.getNodeParameter('revisionKrefGraph', index) as string;
            const maxDepth = this.getNodeParameter('maxDepth', index, 5) as number;
            const edgeTypes = splitCsv(this.getNodeParameter('edgeTypes', index, ''));
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/graph/impact',
              qs: { revision_kref: revisionKref, max_depth: maxDepth, edge_types: edgeTypes },
            });
            out.push({ json: data as IDataObject });
            continue;
          }
        }

        if (operation === 'update') {
          const updateMode = this.getNodeParameter('updateModeRevision', index) as UpdateModeRevision;

          if (updateMode === 'revisionUpdateMetadata') {
            const revisionKref =
              (String(this.getNodeParameter('revisionKrefUpdateMetadata', index, '') ?? '').trim() ||
                String(this.getNodeParameter('krefRevisionUpdate', index, '') ?? '').trim() ||
                String(this.getNodeParameter('kref', index, '') ?? '').trim()) as string;
            const normalizedRevisionKref = normalizeKrefString(this, revisionKref, 'Revision Kref');
            const metadataFromRevisionField = normalizeMetadata(this.getNodeParameter('revisionMetadataUpdate', index, {}));
            const metadataFromLegacyField = normalizeMetadata(this.getNodeParameter('metadata', index, {}));
            const metadata =
              Object.keys(metadataFromRevisionField).length > 0 ? metadataFromRevisionField : metadataFromLegacyField;
            const data = await kumihoRequest(this, {
              method: 'PATCH',
              path: '/api/v1/revisions/by-kref',
              qs: { kref: normalizedRevisionKref },
              body: { metadata },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (updateMode === 'revisionDeprecate') {
            const revisionKref =
              (String(this.getNodeParameter('revisionKrefDeprecate', index, '') ?? '').trim() ||
                String(this.getNodeParameter('krefRevisionUpdate', index, '') ?? '').trim() ||
                String(this.getNodeParameter('kref', index, '') ?? '').trim()) as string;
            const normalizedRevisionKref = normalizeKrefString(this, revisionKref, 'Revision Kref');
            const deprecated = this.getNodeParameter('deprecatedRevision', index, true) as boolean;
            const data = await kumihoRequest(this, {
              method: 'POST',
              path: '/api/v1/revisions/deprecate',
              qs: { kref: normalizedRevisionKref, deprecated },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (updateMode === 'revisionSetTag') {
            const revisionKref =
              (String(this.getNodeParameter('revisionKrefTagUpdate', index, '') ?? '').trim() ||
                String(this.getNodeParameter('kref', index, '') ?? '').trim()) as string;
            const normalizedRevisionKref = normalizeKrefString(this, revisionKref, 'Revision Kref');
            const tag =
              (String(this.getNodeParameter('tagUpdate', index, '') ?? '').trim() ||
                String(this.getNodeParameter('tag', index, '') ?? '').trim()) as string;
            const data = await kumihoRequest(this, {
              method: 'POST',
              path: '/api/v1/revisions/tags',
              qs: { kref: normalizedRevisionKref },
              body: { tag },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (updateMode === 'revisionRemoveTag') {
            const revisionKref =
              (String(this.getNodeParameter('revisionKrefTagUpdate', index, '') ?? '').trim() ||
                String(this.getNodeParameter('kref', index, '') ?? '').trim()) as string;
            const normalizedRevisionKref = normalizeKrefString(this, revisionKref, 'Revision Kref');
            const tag =
              (String(this.getNodeParameter('tagUpdate', index, '') ?? '').trim() ||
                String(this.getNodeParameter('tag', index, '') ?? '').trim()) as string;
            const data = await kumihoRequest(this, {
              method: 'DELETE',
              path: '/api/v1/revisions/tags',
              qs: { kref: normalizedRevisionKref, tag },
            });
            out.push({ json: data as IDataObject });
            continue;
          }
        }

        if (operation === 'delete') {
          const revisionKref =
            (String(this.getNodeParameter('revisionKrefDelete', index, '') ?? '').trim() ||
              String(this.getNodeParameter('kref', index, '') ?? '').trim()) as string;
          const normalizedRevisionKref = normalizeKrefString(this, revisionKref, 'Revision Kref');
          const force = this.getNodeParameter('force', index, false) as boolean;
          const data = await kumihoRequest(this, {
            method: 'DELETE',
            path: '/api/v1/revisions/by-kref',
            qs: { kref: normalizedRevisionKref, force },
          });
          out.push({ json: data as IDataObject });
          continue;
        }
      }

      if (resource === 'artifact') {
        if (operation === 'create') {
          const revisionKref = this.getNodeParameter('artifactRevisionKref', index) as string;
          const name = this.getNodeParameter('name', index) as string;
          const location = this.getNodeParameter('locationCreate', index) as string;
          const metadataFromArtifactField = normalizeMetadata(this.getNodeParameter('artifactMetadataCreate', index, {}));
          const metadataFromLegacyField = normalizeMetadata(this.getNodeParameter('metadata', index, {}));
          const metadata =
            Object.keys(metadataFromArtifactField).length > 0 ? metadataFromArtifactField : metadataFromLegacyField;

          const data = await kumihoRequest(this, {
            method: 'POST',
            path: '/api/v1/artifacts',
            body: { revision_kref: revisionKref, name, location, metadata },
          });
          out.push({ json: data as IDataObject });
          continue;
        }

        if (operation === 'read') {
          const readMode = this.getNodeParameter('readModeArtifact', index) as ReadModeArtifact;
          if (readMode === 'artifactGet') {
            const revisionKrefRaw = this.getNodeParameter('revisionKrefArtifact', index) as string;
            const revisionKref = normalizeKrefString(this, revisionKrefRaw, 'Revision Kref');
            const rawName = this.getNodeParameter('artifactName', index) as string;

            // If no name is provided, resolve the revision kref to determine the default artifact.
            let name = String(rawName ?? '').trim();
            const nameWasProvided = name.length > 0;
            if (!name) {
              // Prefer fetching the revision, since it includes `default_artifact` in most deployments.
              try {
                const revisionData = await kumihoRequest(this, {
                  method: 'GET',
                  path: '/api/v1/revisions/by-kref',
                  qs: { kref: revisionKref },
                });
                const rev = revisionData && typeof revisionData === 'object' ? (revisionData as Record<string, unknown>) : undefined;
                const fromRevisionGet = rev?.default_artifact ?? rev?.defaultArtifact ?? rev?.defaultArtifactName;
                if (typeof fromRevisionGet === 'string' && fromRevisionGet.trim()) {
                  name = fromRevisionGet.trim();
                }
              } catch {
                // ignore
              }

              // Fallback: if no default artifact is set, auto-pick only when there is exactly one artifact.
              if (!name) {
                try {
                  const artifacts = await kumihoRequest(this, {
                    method: 'GET',
                    path: '/api/v1/artifacts',
                    qs: { revision_kref: revisionKref },
                  });
                  if (Array.isArray(artifacts)) {
                    const names = artifacts
                      .map((entry) => (entry && typeof entry === 'object' ? (entry as Record<string, unknown>).name : undefined))
                      .filter((v): v is string => typeof v === 'string' && v.trim().length > 0)
                      .map((v) => v.trim());

                    if (names.length === 1) {
                      name = names[0];
                    } else if (names.length > 1) {
                      throw new NodeOperationError(
                        this.getNode(),
                        `Default artifact is not set for this revision and multiple artifacts exist. Provide Artifact Name. Available: ${names.join(', ')}`,
                      );
                    }
                  }
                } catch (error) {
                  // If listing artifacts fails, fall through to the standard error below.
                  if (error instanceof NodeOperationError) throw error;
                }
              }
            }

            if (!name) {
              throw new NodeOperationError(
                this.getNode(),
                'Artifact Name is required unless the revision has a default artifact (or exactly one artifact exists).',
              );
            }

            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/artifacts/by-kref',
              qs: { revision_kref: revisionKref, name },
            });

            // IMPORTANT: Never override a matched artifact's location with a revision-level default.
            // Prefer the artifact payload itself; only fall back when location is missing.
            const dataObj = data && typeof data === 'object' ? (data as Record<string, unknown>) : {};
            const dataLocationRaw = dataObj.location;
            let location: string | undefined =
              typeof dataLocationRaw === 'string' && dataLocationRaw.trim().length > 0 ? dataLocationRaw : undefined;

            // Fallback 1: list artifacts and find the matching artifact by name (authoritative).
            if (!location) {
              try {
                const artifacts = await kumihoRequest(this, {
                  method: 'GET',
                  path: '/api/v1/artifacts',
                  qs: { revision_kref: revisionKref },
                });

                if (Array.isArray(artifacts)) {
                  const match = artifacts.find((entry) => {
                    if (!entry || typeof entry !== 'object') return false;
                    const entryName = (entry as Record<string, unknown>).name;
                    return typeof entryName === 'string' && entryName.trim() === name;
                  }) as Record<string, unknown> | undefined;

                  const matchLocation = match?.location;
                  if (typeof matchLocation === 'string' && matchLocation.trim().length > 0) {
                    location = matchLocation;
                  }
                }
              } catch {
                // ignore
              }
            }

            // Fallback 2: /resolve (best-effort). Only accept if it resolves to the requested artifact.
            if (!location) {
              try {
                const resolved = await kumihoRequest(this, {
                  method: 'GET',
                  path: '/api/v1/resolve',
                  qs: { kref: revisionKref, a: name },
                });
                const resolvedObj = resolved && typeof resolved === 'object' ? (resolved as Record<string, unknown>) : undefined;
                const resolvedLocation = resolvedObj?.location;
                const resolvedArtifact =
                  resolvedObj?.resolved_artifact ?? resolvedObj?.resolvedArtifact ?? resolvedObj?.resolvedArtifactName;

                const resolvesToRequested = typeof resolvedArtifact === 'string' && resolvedArtifact.trim() === name;
                if (typeof resolvedLocation === 'string' && resolvedLocation.trim().length > 0) {
                  if (!nameWasProvided || resolvesToRequested) {
                    location = resolvedLocation;
                  }
                }
              } catch {
                // ignore
              }
            }

            out.push({
              json: {
                ...(data as IDataObject),
                ...(location ? { location } : {}),
              } as IDataObject,
            });
            continue;
          }

          if (readMode === 'artifactGetByLocation') {
            const location = this.getNodeParameter('locationQuery', index) as string;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/artifacts/by-location',
              qs: { location },
            });
            emitArray(out, data, outputFormat, returnAll, limit);
            continue;
          }
        }

        if (operation === 'update') {
          const updateMode = this.getNodeParameter('updateModeArtifact', index) as UpdateModeArtifact;
          const kref = this.getNodeParameter('artifactKref', index) as string;

          if (updateMode === 'artifactUpdateMetadata') {
            const metadataFromArtifactField = normalizeMetadata(this.getNodeParameter('artifactMetadataUpdate', index, {}));
            const metadataFromLegacyField = normalizeMetadata(this.getNodeParameter('metadata', index, {}));
            const metadata =
              Object.keys(metadataFromArtifactField).length > 0 ? metadataFromArtifactField : metadataFromLegacyField;
            const data = await kumihoRequest(this, {
              method: 'PATCH',
              path: '/api/v1/artifacts/by-kref',
              qs: { kref },
              body: { metadata },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (updateMode === 'artifactDeprecate') {
            const deprecated = this.getNodeParameter('deprecatedArtifact', index, true) as boolean;
            const data = await kumihoRequest(this, {
              method: 'POST',
              path: '/api/v1/artifacts/deprecate',
              qs: { kref, deprecated },
            });
            out.push({ json: data as IDataObject });
            continue;
          }
        }

        if (operation === 'delete') {
          const kref = this.getNodeParameter('artifactKref', index) as string;
          const force = this.getNodeParameter('force', index, false) as boolean;
          const data = await kumihoRequest(this, {
            method: 'DELETE',
            path: '/api/v1/artifacts/by-kref',
            qs: { kref, force },
          });
          out.push({ json: data as IDataObject });
          continue;
        }
      }

      if (resource === 'bundle') {
        if (operation === 'create') {
          const spacePath = normalizeKumihoPath(this, this.getNodeParameter('bundleSpacePath', index), 'Space Path');
          const bundleName = this.getNodeParameter('bundleName', index) as string;
          const metadata = normalizeMetadata(this.getNodeParameter('metadata', index, {}));

          try {
            const data = await kumihoRequest(this, {
              method: 'POST',
              path: '/api/v1/bundles',
              body: { space_path: spacePath, bundle_name: bundleName, metadata },
            });
            out.push({ json: data as IDataObject });
            continue;
          } catch (error) {
            const err = error as unknown as { httpCode?: unknown; kumiho?: { status_code?: unknown } };
            const httpCode = Number(err?.httpCode ?? err?.kumiho?.status_code);
            if (httpCode === 409) {
              try {
                const normalized = spacePath.replace(/^\/+/, '');
                const bundleKref = `kref://${normalized}/${bundleName}.bundle`;
                const existing = await kumihoRequest(this, {
                  method: 'GET',
                  path: '/api/v1/bundles/by-kref',
                  qs: { kref: bundleKref },
                  maxAttempts: 1,
                  timeoutMs: 15000,
                  retryBudgetMs: 15000,
                });
                out.push({ json: existing as IDataObject });
                continue;
              } catch {
                // fall through and throw original error
              }
            }
            throw error;
          }
        }

        if (operation === 'read') {
          const readMode = this.getNodeParameter('readModeBundle', index) as ReadModeBundle;

          if (readMode === 'bundleGet') {
            const spacePath = normalizeKumihoPath(this, this.getNodeParameter('bundleSpacePath', index), 'Space Path');
            const bundleName = this.getNodeParameter('bundleName', index) as string;
            const overrideEnabled = this.getNodeParameter('bundleKrefOverrideEnabled', index, false) as boolean;
            const overrideKref = String(this.getNodeParameter('bundleKrefOverride', index, '') ?? '').trim();

            const normalized = spacePath.replace(/^\/+/, '');
            const computedKref = `kref://${normalized}/${bundleName}.bundle`;
            const bundleKref = overrideEnabled && overrideKref ? overrideKref : computedKref;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/bundles/by-kref',
              qs: { kref: bundleKref },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (readMode === 'bundleListMembers') {
            const bundleKref = this.getNodeParameter('bundleKref', index) as string;
            const revisionNumber = parseOptionalInt(this.getNodeParameter('bundleRevisionNumber', index, ''));
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/bundles/members',
              qs: { bundle_kref: bundleKref, revision_number: revisionNumber },
            });
            emitArray(out, data, outputFormat, returnAll, limit);
            continue;
          }

          if (readMode === 'bundleHistory') {
            const bundleKref = this.getNodeParameter('bundleKref', index) as string;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/bundles/history',
              qs: { bundle_kref: bundleKref },
            });
            emitArray(out, data, outputFormat, returnAll, limit);
            continue;
          }
        }

        if (operation === 'update') {
          const updateMode = this.getNodeParameter('updateModeBundle', index) as UpdateModeBundle;
          const bundleKref = this.getNodeParameter('bundleKref', index) as string;
          const itemKrefInput = this.getNodeParameter('bundleItemKref', index) as string;
          const metadata = normalizeMetadata(this.getNodeParameter('metadata', index, {}));

          const itemKrefs = splitKrefList(itemKrefInput);
          if (!itemKrefs.length) {
            throw new NodeOperationError(this.getNode(), 'Item Kref(s) is required');
          }

          if (updateMode === 'bundleAddMember') {
            for (const itemKref of itemKrefs) {
              try {
                const data = await kumihoRequest(this, {
                  method: 'POST',
                  path: '/api/v1/bundles/members/add',
                  body: { bundle_kref: bundleKref, item_kref: itemKref, metadata },
                });
                out.push({ json: { ...(data as IDataObject), bundle_kref: bundleKref, item_kref: itemKref } });
              } catch (error) {
                const err = error as unknown as { httpCode?: unknown; kumiho?: { status_code?: unknown } };
                const httpCode = Number(err?.httpCode ?? err?.kumiho?.status_code);

                // Treat "already exists" as idempotent success (member already in bundle).
                if (httpCode === 409) {
                  out.push({
                    json: {
                      success: true,
                      message: 'Already exists',
                      new_revision: null,
                      bundle_kref: bundleKref,
                      item_kref: itemKref,
                    } as IDataObject,
                  });
                  continue;
                }

                throw error;
              }
            }
            continue;
          }

          if (updateMode === 'bundleRemoveMember') {
            for (const itemKref of itemKrefs) {
              const data = await kumihoRequest(this, {
                method: 'POST',
                path: '/api/v1/bundles/members/remove',
                body: { bundle_kref: bundleKref, item_kref: itemKref, metadata },
              });
              out.push({ json: { ...(data as IDataObject), bundle_kref: bundleKref, item_kref: itemKref } });
            }
            continue;
          }
        }

        if (operation === 'delete') {
          const bundleKref =
            (String(this.getNodeParameter('bundleKrefDelete', index, '') ?? '').trim() ||
              String(this.getNodeParameter('bundleKref', index, '') ?? '').trim()) as string;
          const normalizedBundleKref = normalizeKrefString(this, bundleKref, 'Bundle Kref');
          const force = this.getNodeParameter('force', index, false) as boolean;
          const data = await kumihoRequest(this, {
            method: 'DELETE',
            path: '/api/v1/bundles/by-kref',
            qs: { kref: normalizedBundleKref, force },
          });
          out.push({ json: data as IDataObject });
          continue;
        }
      }

      if (resource === 'graph') {
        if (operation === 'create') {
          const createMode = this.getNodeParameter('createMode', index) as CreateMode;
          if (createMode === 'graphCreateEdge') {
            const sourceKref = this.getNodeParameter('sourceKref', index) as string;
            const targetKref = this.getNodeParameter('targetKref', index) as string;
            const edgeType = this.getNodeParameter('edgeType', index) as string;
            const metadata = normalizeMetadata(this.getNodeParameter('metadata', index, {}));
            const data = await kumihoRequest(this, {
              method: 'POST',
              path: '/api/v1/graph/edges',
              body: { source_kref: sourceKref, target_kref: targetKref, edge_type: edgeType, metadata },
            });
            out.push({ json: data as IDataObject });
            continue;
          }
        }

        if (operation === 'read') {
          const readMode = this.getNodeParameter('readModeGraph', index) as ReadModeGraph;
          const maxDepth = this.getNodeParameter('maxDepth', index, 5) as number;
          const edgeTypes = splitCsv(this.getNodeParameter('edgeTypes', index, ''));

          if (readMode === 'graphListEdges') {
            const revisionKref = this.getNodeParameter('revisionKrefGraph', index) as string;
            const edgeType = String(this.getNodeParameter('edgeTypeFilter', index, '') ?? '').trim();
            const direction = this.getNodeParameter('direction', index, 0) as number;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/graph/edges',
              qs: { revision_kref: revisionKref, edge_type: edgeType || undefined, direction },
            });
            emitArray(out, data, outputFormat, returnAll, limit);
            continue;
          }

          if (readMode === 'graphGetDependencies') {
            const revisionKref = this.getNodeParameter('revisionKrefGraph', index) as string;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/graph/dependencies',
              qs: { revision_kref: revisionKref, max_depth: maxDepth, edge_types: edgeTypes },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (readMode === 'graphFindPath') {
            const sourceKref = this.getNodeParameter('sourceKref', index) as string;
            const targetKref = this.getNodeParameter('targetKref', index) as string;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/graph/path',
              qs: { source_kref: sourceKref, target_kref: targetKref, max_depth: maxDepth, edge_types: edgeTypes },
            });
            out.push({ json: data as IDataObject });
            continue;
          }

          if (readMode === 'graphAnalyzeImpact') {
            const revisionKref = this.getNodeParameter('revisionKrefGraph', index) as string;
            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/graph/impact',
              qs: { revision_kref: revisionKref, max_depth: maxDepth, edge_types: edgeTypes },
            });
            out.push({ json: data as IDataObject });
            continue;
          }
        }
      }

      if (resource === 'kref') {
        if (operation === 'read') {
          const readMode = this.getNodeParameter('readModeKref', index) as ReadModeKref;
          if (readMode === 'krefResolve') {
            const kref =
              (String(this.getNodeParameter('krefResolveKref', index, '') ?? '').trim() ||
                String(this.getNodeParameter('kref', index, '') ?? '').trim()) as string;
            if (!kref) {
              throw new NodeOperationError(this.getNode(), 'Kref is required');
            }
            const r = parseOptionalInt(this.getNodeParameter('krefResolveRevisionNumber', index, ''));
            const t = String(this.getNodeParameter('krefResolveTag', index, '') ?? '').trim() || undefined;
            const a = String(this.getNodeParameter('krefResolveArtifactName', index, '') ?? '').trim() || undefined;

            const qs: Record<string, string | number | boolean | string[] | undefined> = { kref };
            if (r !== undefined) qs.r = r;
            if (t !== undefined) qs.t = t;
            if (a !== undefined) qs.a = a;

            const data = await kumihoRequest(this, {
              method: 'GET',
              path: '/api/v1/resolve',
              qs,
            });

            // If `a` isn't provided, /resolve can still return a location (via default artifact)
            // but may not report which artifact was used. Best-effort enrich from /resolve/revision.
            if (a === undefined && data && typeof data === 'object') {
              const dataObj = data as Record<string, unknown>;
              const hasResolvedArtifact =
                typeof dataObj.resolved_artifact === 'string' && dataObj.resolved_artifact.trim().length > 0;
              const hasResolvedRevision = typeof dataObj.resolved_revision === 'number';

              if (!hasResolvedArtifact || !hasResolvedRevision) {
                try {
                  const revResolved = await kumihoRequest(this, {
                    method: 'GET',
                    path: '/api/v1/resolve/revision',
                    qs: { kref, t },
                  });
                  const revision =
                    revResolved && typeof revResolved === 'object'
                      ? ((revResolved as Record<string, unknown>).revision as Record<string, unknown> | undefined)
                      : undefined;
                  const defaultArtifact =
                    revision?.default_artifact ?? revision?.defaultArtifact ?? revision?.defaultArtifactName;
                  const revisionNumber = revision?.number;
                  out.push({
                    json: {
                      ...(data as IDataObject),
                      ...(typeof defaultArtifact === 'string' && defaultArtifact.trim().length > 0
                        ? { resolved_artifact: defaultArtifact.trim() }
                        : {}),
                      ...(typeof revisionNumber === 'number' ? { resolved_revision: revisionNumber } : {}),
                    } as IDataObject,
                  });
                  continue;
                } catch {
                  // Ignore enrichment failures; return the original resolve response.
                }
              }
            }

            out.push({ json: data as IDataObject });
            continue;
          }
        }
      }

      throw new NodeOperationError(this.getNode(), `Unsupported combination: resource=${resource}, operation=${operation}`);
    }

    return [out];
  }
}
