"""Export the hosted SDK tool schemas plus proposed Muse review classifications.

Run with the cloud-mcp dependencies installed. No tools are called and no user
credentials, production memories, or network responses are read.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import importlib.metadata
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'cloud-mcp'))
os.environ['KUMIHO_MCP_HOSTED'] = '1'

# Explicit classifications: an SDK upgrade must not silently classify a new tool.
WRITES = {
    'kumiho_memory_store': 'May create or revise a memory, move its published revision and supersede prior content.',
    'kumiho_create_space': 'Creates a space in the authorized project.',
    'kumiho_deprecate_item': 'Reversibly retires an item from ordinary recall; not permanent erasure.',
    'kumiho_memory_reflect': 'Stores typed captures or corrections and a short response buffer. May revise a named item.',
    'kumiho_memory_space_profile': 'Persists a profile of a memory space; not a read-only analysis.',
    'kumiho_memory_decompose': 'Writes typed graph nodes and relationships; can supersede earlier relationships.',
}
SENSITIVE = {
    'kumiho_chat_clear': 'Irreversibly clears the current conversation buffer.',
    'kumiho_memory_consolidate': 'Stores a durable summary AND clears the current buffer. This combined operation includes irreversible deletion.',
}
READS = {
    'kumiho_list_projects', 'kumiho_get_spaces', 'kumiho_get_item',
    'kumiho_search_items', 'kumiho_memory_retrieve', 'kumiho_get_revision_by_tag',
    'kumiho_get_provenance_summary', 'kumiho_chat_get',
    'kumiho_memory_recall', 'kumiho_memory_engage',
}

def review(tool):
    name = tool['name']
    annotation = tool.get('annotations') or {}
    if name in READS:
        if annotation.get('readOnlyHint') is not True:
            raise ValueError(f'{name} no longer read-only; review required')
        return {'classification': 'Read', 'side_effects': 'No application data mutation.'}
    if name in SENSITIVE or name in WRITES:
        if annotation.get('readOnlyHint') is not False:
            raise ValueError(f'{name} annotation changed; review required')
        return {'classification': 'Sensitive write' if name in SENSITIVE else 'Write',
                'side_effects': (SENSITIVE | WRITES)[name]}
    raise ValueError(f'Unreviewed tool: {name}')

async def export():
    from kumiho_cloud_mcp._compat import build_server, listed_tools
    tools = [t.model_dump(mode='json', by_alias=True, exclude_none=True) for t in await listed_tools(build_server())]
    names = [t['name'] for t in tools]
    if len(names) != len(set(names)) or set(names) != READS | WRITES.keys() | SENSITIVE.keys():
        raise ValueError('Hosted catalog changed; review all classifications before export')
    return {'format': 'Kumiho reviewer reference; not a Meta portal import format',
            'endpoint': 'https://mcp.kumiho.cloud/mcp',
            'sdk_versions': {name: importlib.metadata.version(name) for name in ('kumiho', 'kumiho-memory', 'mcp')},
            'classification_status': 'Proposed for Meta review; not a host permission enforcement mechanism',
            'tools': [{**t, 'muse_review': review(t)} for t in tools]}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    document = json.dumps(asyncio.run(export()), ensure_ascii=False, indent=2) + '\n'
    output = Path(__file__).with_name('tools.json')
    if args.check:
        if not output.exists() or output.read_text(encoding='utf-8') != document:
            raise SystemExit('muse/tools.json differs from the installed hosted runtime; regenerate and review')
        print('Muse tool catalog matches the installed hosted runtime (18 tools)')
    else:
        output.write_text(document, encoding='utf-8')
        print(f'Exported {output}')

if __name__ == '__main__':
    main()
