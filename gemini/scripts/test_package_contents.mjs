import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, cpSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { assertPackageContents } from './package_contents.mjs';

function fixture(t) {
  const base = mkdtempSync(join(tmpdir(), 'kumiho-package-equality-'));
  t.after(() => rmSync(base, {recursive:true, force:true}));
  const source = join(base,'source'), installed = join(base,'installed');
  mkdirSync(join(source,'skills','memory-capture'), {recursive:true});
  mkdirSync(join(source,'commands','kumiho'), {recursive:true});
  writeFileSync(join(source,'gemini-extension.json'), '{"name":"kumiho-memory"}');
  writeFileSync(join(source,'skills','memory-capture','SKILL.md'), 'Approved capture scope');
  writeFileSync(join(source,'commands','kumiho','remember.toml'), 'prompt = "Authorized saves only"');
  cpSync(source,installed,{recursive:true});
  return {source,installed};
}

test('accept identical shipped files plus host installation metadata', t => {
  const f=fixture(t);
  writeFileSync(join(f.installed,'.gemini-extension-install.json'),'{"source":"test"}');
  assertPackageContents(f.source,f.installed);
});
test('reject altered skill with unchanged manifest and skill name', t => {
  const f=fixture(t);
  writeFileSync(join(f.installed,'skills','memory-capture','SKILL.md'),'Changed storage policy');
  assert.throws(()=>assertPackageContents(f.source,f.installed), /content differs/);
});
test('reject altered command despite matching manifest', t => {
  const f=fixture(t);
  writeFileSync(join(f.installed,'commands','kumiho','remember.toml'),'prompt = "Changed behavior"');
  assert.throws(()=>assertPackageContents(f.source,f.installed), /content differs/);
});
test('reject unexpected extra executable resource', t => {
  const f=fixture(t);
  writeFileSync(join(f.installed,'unexpected.mjs'),'process.exit(1)');
  assert.throws(()=>assertPackageContents(f.source,f.installed), /file set differs/);
});
