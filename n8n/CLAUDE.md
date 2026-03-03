# CLAUDE.md - kumiho-n8n

This file provides guidance for AI agents working with the kumiho-n8n nodes package.

## Project Overview

**kumiho-n8n** provides custom n8n nodes for integrating Kumiho Cloud into n8n workflows. It includes:
- Kumiho API credentials
- Kumiho Action node (CRUD operations)
- Kumiho Event Stream trigger (real-time events)

**Version:** 0.4.4
**License:** MIT
**n8n API Version:** 1

## Tech Stack

- **n8n:** Community node package
- **TypeScript:** 5.9
- **Build Tool:** @n8n/node-cli

## Build & Test Commands

### Development
```bash
npm install                    # Install dependencies
npm run dev                    # Development mode (watch)
npm run build                  # Build for production
```

### Lint
```bash
npm run lint                   # ESLint check
npm run lint:fix               # ESLint with auto-fix
```

### Local Testing
```bash
# Build and run local n8n with custom nodes
./scripts/run-local-n8n.sh

# Build only (don't start n8n)
./scripts/run-local-n8n.sh --no-start
```

### Release
```bash
npm run release                # Prepare release
npm run prepublishOnly         # Pre-publish checks
```

## Project Structure

```
kumiho-n8n/
├── credentials/
│   └── KumihoApi.credentials.ts   # API credentials definition
├── nodes/
│   ├── KumihoAction/
│   │   └── KumihoAction.node.ts   # CRUD operations node
│   ├── KumihoEventStream/
│   │   └── KumihoEventStreamTrigger.node.ts  # Event trigger
│   └── images/                     # Node icons
│       ├── Project.png
│       ├── Space.png
│       ├── Graph.png
│       └── ...
├── scripts/
│   └── run-local-n8n.sh           # Local testing script
├── dist/                          # Build output
├── package.json
└── tsconfig.json
```

## Nodes

### KumihoAction
CRUD operations for Kumiho Cloud resources:
- Projects
- Spaces
- Items
- Revisions
- Artifacts
- Bundles
- Edges

### KumihoEventStreamTrigger
Real-time event trigger for workflow automation:
- Listen to graph events
- Filter by Kref patterns
- Handle creates, updates, deletes

## Credentials

The `KumihoApi` credential type requires:
- **Server URL:** Kumiho server endpoint
- **API Token:** Firebase/Control Plane JWT

## Local Development Script

`scripts/run-local-n8n.sh` automates local testing:

1. Cleans dist folder
2. Builds the nodes package
3. Copies icons to dist
4. Installs to `~/.n8n/custom`
5. Starts n8n with custom nodes

### Usage
```bash
# Full workflow (build + start n8n)
./scripts/run-local-n8n.sh

# Build only
./scripts/run-local-n8n.sh --no-start

# Help
./scripts/run-local-n8n.sh --help
```

### Environment Variables (for local n8n)
```bash
N8N_LOG_LEVEL=debug
N8N_LOG_OUTPUT=console
N8N_CUSTOM_EXTENSIONS=~/.n8n/custom/node_modules
N8N_RESTRICT_FILE_ACCESS_TO=~/.n8n-files
```

## Publishing

This package is published to npm as `n8n-nodes-kumiho`:

```bash
# Prepare and publish
npm run release
npm publish
```

Users can install via n8n UI or:
```bash
npm install n8n-nodes-kumiho
```

## n8n Node Development

### Node Structure
```typescript
import { INodeType, INodeTypeDescription } from 'n8n-workflow';

export class KumihoAction implements INodeType {
  description: INodeTypeDescription = {
    displayName: 'Kumiho',
    name: 'kumihoAction',
    icon: 'file:kumiho.svg',
    group: ['transform'],
    version: 1,
    // ... configuration
  };

  async execute(this: IExecuteFunctions) {
    // Implementation
  }
}
```

### Trigger Node
```typescript
export class KumihoEventStreamTrigger implements INodeType {
  description: INodeTypeDescription = {
    displayName: 'Kumiho Event Stream',
    name: 'kumihoEventStreamTrigger',
    group: ['trigger'],
    // ...
  };

  async trigger(this: ITriggerFunctions) {
    // Event stream implementation
  }
}
```

## Code Style

- TypeScript strict mode
- ESLint configuration via @n8n/node-cli
- Follow n8n node development conventions
- Use proper error handling with n8n error types

## Troubleshooting

### Nodes not appearing in n8n
1. Hard refresh browser (Cmd+Shift+R)
2. Check n8n logs for loading errors
3. Verify package is in `~/.n8n/custom/node_modules`

### Build errors
```bash
rm -rf node_modules dist
npm install
npm run build
```

### Icon not showing
Ensure icons are copied to `dist/nodes/<NodeName>/` after build.
