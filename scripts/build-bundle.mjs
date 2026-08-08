#!/usr/bin/env node
// build-bundle — compile the node/TS services into self-contained JS artifacts.
//
// WHY: the services run in dev via `npx tsx src/<x>.ts`, which needs node + tsx +
// the full node_modules (~213 MB) at runtime. For the bundled desktop app that's
// too heavy and requires tsx on PATH. This produces `dist/<service>.js` with deps
// inlined (~2.6 MB for voice-agent), so a packaged install runs them with plain
// node: `<bundled-node> dist/voice-agent.js` — no tsx, no node_modules.
// Consumed by ag2space-cinny-desktop packaging (build-services.sh → this script).
//
// NODE VERSION: target node22 — `src/conversation-store.ts` imports `node:sqlite`,
// which is only a builtin on node >= 22.5 (v20 LTS dies at boot with
// ERR_UNKNOWN_BUILTIN_MODULE). The bundled node runtime must be >= 22.5.
//
// MANIFEST SKILL TOOLS: also compiled here, to `dist/skills/<name>/tools.js`.
// A manifest declares `"tools": "./tools.ts"`, which imports only under tsx — so
// under a bundled artifact (plain node) every one of them died with
// `Unknown file extension ".ts"`, was caught and warned, and the tools silently
// never registered. Measured on one host: 11 consecutive voice boots with all
// four skills failing, while the system prompt kept telling the model that
// summon/dismiss/join_zoom were callable. src/inline-tools.ts prefers these
// artifacts (see skillToolsCandidates) and falls back to the declared path.
//
// This supersedes the previous FOLLOW-UP note here, which read: "a couple of
// manifest-skill tools.ts (obsidian-vault, screen-companion) dynamic-import
// src/*.js at runtime; those relative paths don't resolve from a bundled
// artifact." Checked: obsidian-vault's dynamic import is `node:child_process`, a
// builtin, so it was never affected. screen-companion's IS `src/inline-tools.js`,
// and bundling resolves it — but that inlines a second copy of the skill loader
// into the artifact, whose top-level await would re-enter the loader and deadlock
// on an ESM cycle. inline-tools.ts guards that via SUTANDO_SKILL_LOADER_ACTIVE.
// The embedding itself is still a design smell worth removing at the source.

import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { statSync, existsSync, readdirSync, readFileSync } from 'node:fs';
import {
  BROWSER_TRANSPORT_ARTIFACT,
  BROWSER_TRANSPORT_ENTRY,
  browserTransportOptions,
} from './browser-transport-build.mjs';

const repo = join(dirname(fileURLToPath(import.meta.url)), '..');

// Entrypoints that run on node (TypeScript via tsx today).
const ENTRYPOINTS = [
  'src/voice-agent.ts',
  'src/web-client.ts',
  'skills/phone-conversation/scripts/conversation-server.ts',
  'skills/quota-tracker/scripts/credential-proxy.ts',
  'src/observability/boot.ts',
  // Track 9: the call-tiers emitter must run on desktop installs too, where
  // launch-sutando.sh skips startup.sh and there is no tsx — the launcher
  // spawns this dist with the bundled node instead (one emitter impl, no
  // second-language rewrite; the #2129 single-instance PID guard carries over).
  'src/emit-call-tiers.ts',
];

// ESM output + some CJS deps (e.g. dotenv) that call require() / use __dirname.
// Shim them so the bundled ESM artifact runs under plain node.
const CJS_INTEROP_BANNER =
  "import{createRequire as __cr}from'module';" +
  "import{fileURLToPath as __fu}from'url';" +
  "import{dirname as __dn}from'path';" +
  'const require=__cr(import.meta.url);' +
  'const __filename=__fu(import.meta.url);' +
  'const __dirname=__dn(__filename);';

// ws ships optional native accelerators; they're not required for correctness.
const NATIVE_EXTERNAL = ['bufferutil', 'utf-8-validate'];

const human = (n) => (n < 1024 * 1024 ? `${(n / 1024).toFixed(0)}KB` : `${(n / 1024 / 1024).toFixed(1)}MB`);

// Manifest skills whose `tools` entry is TypeScript. Discovered rather than
// hardcoded: a hardcoded list silently stops covering a skill added later, and
// this exact class of bug (a declared entry nothing compiles) is what the block
// above documents. Only in-repo skills — a workspace/external skill is not ours
// to compile, and inline-tools only offers the dist artifact for repo skills.
function discoverSkillToolEntries() {
  const out = [];
  const skillsRoot = join(repo, 'skills');
  let names = [];
  try {
    names = readdirSync(skillsRoot).filter((n) => {
      try { return statSync(join(skillsRoot, n)).isDirectory(); } catch { return false; }
    });
  } catch { return out; }
  for (const name of names.sort()) {
    const manifestPath = join(skillsRoot, name, 'manifest.json');
    if (!existsSync(manifestPath)) continue;
    let manifest;
    try { manifest = JSON.parse(readFileSync(manifestPath, 'utf8')); } catch { continue; }
    if (!manifest.enabled || typeof manifest.tools !== 'string') continue;
    const rel = manifest.tools.replace(/^\.\//, '');
    if (!rel.endsWith('.ts')) continue;
    const entry = join(skillsRoot, name, rel);
    if (existsSync(entry)) out.push({ name, entry, rel });
  }
  return out;
}

let failed = false;
for (const entry of ENTRYPOINTS) {
  const base = entry.split('/').pop().replace(/\.ts$/, '');
  const outfile = join(repo, 'dist', `${base}.js`);
  try {
    await build({
      entryPoints: [join(repo, entry)],
      outfile,
      bundle: true,
      platform: 'node',
      format: 'esm',
      target: 'node22', // node:sqlite requires >= 22.5 (conversation-store)
      banner: { js: CJS_INTEROP_BANNER },
      external: NATIVE_EXTERNAL,
      logLevel: 'warning',
    });
    console.log(`  ✓ ${entry.padEnd(52)} → dist/${base}.js  ${human(statSync(outfile).size)}`);
  } catch (err) {
    console.error(`  ✗ ${entry} — ${err.message}`);
    failed = true;
  }
}

// Manifest skill tools → dist/skills/<name>/tools.js. Same node/ESM settings and
// CJS banner as the services above: without the banner screen-companion's bundle
// dies at import with `Dynamic require of "process" is not supported` (verified).
// resolveExtensions includes '.ts' because a skill's own imports are written with
// `.js` specifiers that only exist as TypeScript on disk.
// A failure here does NOT fail the bundle: these are optional voice surfaces, and
// the loader still falls back to the declared path under tsx. It warns loudly
// instead — silent absence is the bug being fixed, so it must not be reintroduced
// at the build layer.
for (const { name, entry, rel } of discoverSkillToolEntries()) {
  const outfile = join(repo, 'dist', 'skills', name, rel.replace(/\.ts$/, '.js'));
  const shown = `skills/${name}/${rel}`;
  try {
    await build({
      entryPoints: [entry],
      outfile,
      bundle: true,
      platform: 'node',
      format: 'esm',
      target: 'node22',
      banner: { js: CJS_INTEROP_BANNER },
      external: NATIVE_EXTERNAL,
      resolveExtensions: ['.ts', '.js', '.mjs', '.json'],
      logLevel: 'warning',
    });
    console.log(`  ✓ ${shown.padEnd(52)} → dist/skills/${name}/tools.js  ${human(statSync(outfile).size)}`);
  } catch (err) {
    console.warn(`  ⚠ ${shown} — ${err.message} (skill tools will not load under plain node)`);
  }
}

// The browser voice transport is a SEPARATE build: platform:browser + IIFE, not
// node/ESM. The packaged web-client serves this file to the page; without it the
// voice UI has no transport at all (there is no inline copy to fall back to), so
// a failure here fails the whole bundle rather than warning.
{
  const outfile = join(repo, 'dist', BROWSER_TRANSPORT_ARTIFACT);
  try {
    await build(browserTransportOptions({ outfile }));
    console.log(
      `  ✓ ${BROWSER_TRANSPORT_ENTRY.padEnd(52)} → dist/${BROWSER_TRANSPORT_ARTIFACT}  ${human(statSync(outfile).size)}  [browser/iife]`,
    );
  } catch (err) {
    console.error(`  ✗ ${BROWSER_TRANSPORT_ENTRY} (browser) — ${err.message}`);
    failed = true;
  }
}

if (failed) {
  console.error('\nbuild:bundle FAILED');
  process.exit(1);
}
console.log('\nbuild:bundle OK — run artifacts with: <node>=22.5+ dist/<service>.js');
