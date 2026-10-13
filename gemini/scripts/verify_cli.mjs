import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const pkg = resolve(process.argv[2] ?? join(root, 'cli'));
const binary = resolve(process.argv[3] ?? '');
const installSource = process.argv[4] ?? pkg;
assert(process.argv[3], 'Usage: node verify_cli.mjs <extension-directory> <gemini.js>');
const manifest = JSON.parse(readFileSync(join(pkg, 'gemini-extension.json'), 'utf8'));
assert.equal(manifest.name, 'kumiho-memory');
assert.equal(manifest.mcpServers['kumiho-memory'].httpUrl, 'https://mcp.kumiho.cloud/mcp');
assert.deepEqual(manifest.mcpServers['kumiho-memory'].oauth, {enabled:true, scopes:['memory']});
assert(!('trust' in manifest.mcpServers['kumiho-memory']), 'No extension trust bypass');
assert(!('headers' in manifest.mcpServers['kumiho-memory']), 'No embedded bearer');
const expected = ['dream-state','kumiho-backfill','kumiho-memory','kumiho-onboard','kumiho-personalize','memory-capture'];
assert.deepEqual(readdirSync(join(pkg,'skills')).sort(), expected);
const isolated = mkdtempSync(join(tmpdir(), 'kumiho-gemini-loader-'));
const working = join(isolated, 'work');
mkdirSync(working);
mkdirSync(join(isolated, '.gemini'));
const settings = {mcp:{excluded:['kumiho-memory']}, telemetry:{enabled:false}};
writeFileSync(join(isolated,'.gemini','settings.json'), JSON.stringify(settings));
// The wrapper reads a base-directory settings file; the CLI itself uses a home.
writeFileSync(join(isolated,'settings.json'), JSON.stringify(settings));
const env = {...process.env, GEMINI_CLI_HOME:isolated, GEMINI_CLI_NO_RELAUNCH:'true'};
for (const key of Object.keys(env)) {
  if (/^(GEMINI_API_KEY|GOOGLE_API_KEY|GOOGLE_APPLICATION_CREDENTIALS|GOOGLE_GENAI_USE_|GEMINI_CLI_AUTH_OVERRIDE|KUMIHO_)/.test(key)) delete env[key];
}
function run(args, input='') {
 const r = spawnSync(process.execPath, [binary,...args], {cwd:working,env,input,encoding:'utf8',timeout:60000});
 assert(!r.error, String(r.error));
 assert.equal(r.status,0,r.stderr+r.stdout);
 assert(!/Starting OAuth|Opening authentication page/.test(r.stdout+r.stderr), 'Offline loader test must not authenticate');
 return r.stdout+r.stderr;
}
assert.match(run(['--version']), /0\.64\.0/);
assert.match(run(['extensions','install',installSource,'--consent'], 'y\n'), /installed successfully/);
const list = run(['extensions','list']);
assert.match(list, /kumiho-memory \(0\.1\.0\)/);
assert.match(list, /GEMINI\.md/);
for (const skill of expected) assert(list.includes(skill+':'), 'Extension loader omitted '+skill);
const skills = run(['skills','list']);
for (const skill of expected) assert(skills.includes(skill), 'Skill discovery omitted '+skill);
const installed = join(isolated,'.gemini','extensions','kumiho-memory');
assert.deepEqual(JSON.parse(readFileSync(join(installed,'gemini-extension.json'),'utf8')),manifest);
run(['extensions','uninstall','kumiho-memory']);
assert.match(run(['extensions','list']), /No extensions installed/);
console.log(JSON.stringify({cli:'0.64.0',extension:manifest.version,install:true,skillCount:expected.length,uninstall:true,networkMcp:'excluded for offline QA'}));
