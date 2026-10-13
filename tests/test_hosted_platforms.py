"""Contract checks for the new hosted platform packages (no real account writes)."""
import importlib.util
import json
import re
import unittest
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'grokbot'

def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

spec = importlib.util.spec_from_file_location('muse_export', ROOT / 'muse/export_tools.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

class HostedPlatforms(unittest.TestCase):
    def test_official_portable_schemas(self):
        for name in ('plugin', 'mcp'):
            schema = read_json(ROOT / 'tests/schemas/agent-plugins-1.0.0' / f'{name}.schema.json')
            jsonschema.Draft202012Validator(schema).validate(read_json(PKG / f'{name}.json'))

    def test_marketplace_resolves_self_contained_package(self):
        entries = read_json(ROOT / '.cursor-plugin/marketplace.json')['plugins']
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        folder = (ROOT / entry['source']).resolve()
        folder.relative_to(ROOT)
        manifest = read_json(folder / '.cursor-plugin/plugin.json')
        portable = read_json(folder / 'plugin.json')
        for name in ('name', 'version', 'description'):
            self.assertEqual(manifest[name], portable[name])
        self.assertEqual(entry['name'], manifest['name'])
        for key in ('logo', 'skills', 'mcpServers'):
            target = (folder / manifest[key]).resolve()
            target.relative_to(folder)
            self.assertTrue(target.exists(), key)
        server = read_json(folder / manifest['mcpServers'])['mcpServers']['kumiho-memory']
        self.assertEqual(server, {'type':'streamable-http','url':'https://mcp.kumiho.cloud/mcp'})

    def test_all_skills_resolve_tools_and_local_references(self):
        tools = {t['name'] for t in read_json(ROOT / 'muse/tools.json')['tools']}
        skills = list((PKG/'skills').glob('*/SKILL.md'))
        self.assertEqual(len(skills), 6)
        for path in skills:
            text = path.read_text(encoding='utf-8-sig')
            front = yaml.safe_load(text.split('---', 2)[1])
            self.assertEqual(front['name'], path.parent.name)
            self.assertTrue(front['description'])
            self.assertFalse(set(re.findall(r'`(kumiho_[a-z_]+)`', text)) - tools)
            for ref in re.findall(r'\]\(([^)]+)\)', text):
                if '://' not in ref and not ref.startswith('#'):
                    resolved = (path.parent / ref).resolve()
                    resolved.relative_to(PKG)
                    self.assertTrue(resolved.exists(), ref)

    def test_muse_catalog_matches_authoritative_profile(self):
        from kumiho_cloud_mcp.connector_profile import CONNECTOR_TOOL_ANNOTATIONS
        tools = read_json(ROOT/'muse/tools.json')['tools']
        self.assertEqual({t['name'] for t in tools}, set(CONNECTOR_TOOL_ANNOTATIONS))
        for tool in tools:
            self.assertEqual(tool['annotations'], CONNECTOR_TOOL_ANNOTATIONS[tool['name']])
            self.assertEqual(tool['muse_review'], exporter.review(tool))
            jsonschema.Draft202012Validator.check_schema(tool['inputSchema'])
            self.assertEqual(tool['_meta']['securitySchemes'], [{'type':'oauth2','scopes':['memory']}])

    def test_new_or_changed_tool_requires_review(self):
        with self.assertRaises(ValueError):
            exporter.review({'name':'unreviewed_tool'})
        with self.assertRaises(ValueError):
            exporter.review({'name':'kumiho_memory_engage','annotations':{'readOnlyHint':False}})
        with self.assertRaises(ValueError):
            exporter.review({'name':'kumiho_memory_reflect','annotations':{'readOnlyHint':True}})
        for name in ('kumiho_chat_clear','kumiho_memory_consolidate'):
            self.assertEqual(exporter.review({'name':name,'annotations':{'readOnlyHint':False}})['classification'], 'Sensitive write')

if __name__ == '__main__':
    unittest.main()
