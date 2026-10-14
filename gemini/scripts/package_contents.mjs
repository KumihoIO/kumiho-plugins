import assert from 'node:assert/strict';
import { readdirSync, lstatSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

function inventory(root, installed = false, relative = '') {
  const files = new Map();
  for (const name of readdirSync(join(root, relative)).sort()) {
    const path = relative ? relative + '/' + name : name;
    if (installed && path === '.gemini-extension-install.json') continue;
    const absolute = join(root, path);
    const stat = lstatSync(absolute);
    assert(!stat.isSymbolicLink(), 'Linked package path: ' + path);
    if (stat.isDirectory()) {
      for (const [file, data] of inventory(root, installed, path)) files.set(file, data);
    } else {
      assert(stat.isFile(), 'Non-regular package path: ' + path);
      files.set(path, readFileSync(absolute));
    }
  }
  return files;
}

export function assertPackageContents(source, installed) {
  const expected = inventory(source);
  const actual = inventory(installed, true);
  assert.deepEqual([...actual.keys()].sort(), [...expected.keys()].sort(), 'Installed package file set differs');
  for (const [path, data] of expected) {
    assert(actual.get(path).equals(data), 'Installed package content differs: ' + path);
  }
}
