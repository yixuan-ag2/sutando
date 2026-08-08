// Manifest skill tools must load under the plain node that runs the bundled
// artifacts, where a declared `.ts` entry cannot be imported at all.

import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const CJS_BANNER =
	"import{createRequire as __cr}from'module';import{fileURLToPath as __fu}from'url';" +
	"import{dirname as __dn}from'path';const require=__cr(import.meta.url);" +
	'const __filename=__fu(import.meta.url);const __dirname=__dn(__filename);';

const failures = [];
const check = (name, fn) => {
	try { fn(); console.log(`  ok   ${name}`); }
	catch (err) { console.log(`  FAIL ${name}`); failures.push(`${name}: ${err.message}`); }
};
const checkAsync = async (name, fn) => {
	try { await fn(); console.log(`  ok   ${name}`); }
	catch (err) { console.log(`  FAIL ${name}`); failures.push(`${name}: ${err.message}`); }
};

// The artifact MUST be emitted into <repo>/dist: inline-tools derives REPO_ROOT
// from its own module URL, so a copy elsewhere cannot find <repo>/dist/skills.
const TEST_ARTIFACTS = [];
const { skillToolsCandidates } = await (async () => {
	const out = join(REPO, 'dist', `.test-inline-tools-${process.pid}.mjs`);
	TEST_ARTIFACTS.push(out);
	await build({
		entryPoints: [join(REPO, 'src', 'inline-tools.ts')],
		outfile: out, bundle: true, platform: 'node', format: 'esm', target: 'node22',
		banner: { js: CJS_BANNER }, external: ['bufferutil', 'utf-8-validate'], logLevel: 'silent',
	});
	return import(out);
})();

// ── A. candidate ORDER: a compiled artifact must be preferred over the .ts ──────
check('A1 built dist artifact is preferred over the declared .ts', () => {
	const dirName = 'zoom';
	const built = join(REPO, 'dist', 'skills', dirName, 'tools.js');
	if (!existsSync(built)) {
		// The dist candidate is only offered when the file exists, so without a
		// build there is nothing to assert. Say so rather than passing vacuously.
		throw new Error(`precondition: ${built} missing — run \`npm run build:bundle\` first`);
	}
	const got = skillToolsCandidates(join(REPO, 'skills'), dirName, './tools.ts');
	assert.equal(got[0], built, `expected built artifact first, got ${got[0]}`);
	assert.ok(got.at(-1).endsWith('.ts'), 'declared .ts must remain the last fallback (tsx path)');
});

check('A2 a precompiled sibling .js outranks even the dist artifact', () => {
	const tmp = mkdtempSync(join(tmpdir(), 'skills-'));
	mkdirSync(join(tmp, 'mine'), { recursive: true });
	writeFileSync(join(tmp, 'mine', 'tools.js'), 'export const tools = [];');
	const got = skillToolsCandidates(tmp, 'mine', './tools.ts');
	assert.ok(got[0].endsWith(join('mine', 'tools.js')), `sibling .js should win, got ${got[0]}`);
	rmSync(tmp, { recursive: true, force: true });
});

check('A3 a non-.ts entry is passed through untouched (no invented candidates)', () => {
	const got = skillToolsCandidates(join(REPO, 'skills'), 'zoom', './tools.js');
	assert.equal(got.length, 1, `expected exactly one candidate, got ${JSON.stringify(got)}`);
	assert.ok(got[0].endsWith('tools.js'));
});

// ── B. the dist artifact must NOT leak across skill sources ─────────────────────
check('B1 a non-repo skills dir never resolves to the repo dist artifact', () => {
	// A workspace / external-plugin skill that happens to share a name with an
	// in-repo skill must not silently execute the repo's compiled copy.
	const tmp = mkdtempSync(join(tmpdir(), 'foreign-'));
	mkdirSync(join(tmp, 'zoom'), { recursive: true });
	const got = skillToolsCandidates(tmp, 'zoom', './tools.ts');
	const leaked = got.filter(p => p.startsWith(join(REPO, 'dist')));
	assert.deepEqual(leaked, [], `repo dist leaked into a foreign skills dir: ${leaked}`);
	assert.equal(got.length, 1, 'only the declared path should remain');
	rmSync(tmp, { recursive: true, force: true });
});

// C. the re-entry guard. Defensive: no skill re-enters at module scope today, but
// a cycle through a top-level await would deadlock rather than throw.
check('C1 the guard is cross-instance (env var, not a module-scope flag)', () => {
	const src = readFileSync(join(REPO, 'src', 'inline-tools.ts'), 'utf8');
	assert.ok(src.includes("SKILL_LOADER_ACTIVE_ENV = 'SUTANDO_SKILL_LOADER_ACTIVE'"),
		'guard env var missing');
	assert.ok(/process\.env\[SKILL_LOADER_ACTIVE_ENV\]\s*===\s*'1'/.test(src),
		'guard is never READ — a set-but-unread flag guards nothing');
	assert.ok(src.includes('delete process.env[SKILL_LOADER_ACTIVE_ENV]'),
		'guard is never cleared — a second legitimate scan would return empty forever');
	// The clear must be in a finally: a throw mid-scan would otherwise leave the
	// process permanently unable to load any skill tool.
	assert.ok(/finally\s*\{\s*\n?\s*delete process\.env\[SKILL_LOADER_ACTIVE_ENV\]/.test(src),
		'guard cleared outside a finally — a mid-scan throw would wedge all later scans');
});

// ── D. end-to-end under plain node (the production shape) ───────────────────────
await checkAsync('D1 bundled probe under plain node exposes the skill tools', async () => {
	for (const s of ['zoom', 'obsidian-vault', 'screen-companion']) {
		const a = join(REPO, 'dist', 'skills', s, 'tools.js');
		if (!existsSync(a)) throw new Error(`precondition: ${a} missing — run \`npm run build:bundle\``);
	}
	const dir = mkdtempSync(join(tmpdir(), 'probe-'));
	const entry = join(dir, 'probe.ts');  // source may live anywhere...
	writeFileSync(entry, [
		`import { inlineTools, ownerOnlyTools } from ${JSON.stringify(join(REPO, 'src', 'inline-tools.ts'))};`,
		'const names = [...inlineTools, ...(ownerOnlyTools ?? [])].map((t: any) => t.name);',
		'console.log("TOOLS:" + JSON.stringify(names));',
		'process.exit(0);',
	].join('\n'));
	// ...but the ARTIFACT must sit in <repo>/dist so REPO_ROOT resolves to the repo
	// (see the note above the skillToolsCandidates import).
	const out = join(REPO, 'dist', `.test-probe-${process.pid}.js`);
	TEST_ARTIFACTS.push(out);
	await build({
		entryPoints: [entry], outfile: out, bundle: true, platform: 'node', format: 'esm',
		target: 'node22', banner: { js: CJS_BANNER },
		external: ['bufferutil', 'utf-8-validate'], logLevel: 'silent',
	});
	const { execFileSync } = await import('node:child_process');
	// process.execPath is plain node — no tsx loader — which is the whole point.
	const stdout = execFileSync(process.execPath, [out], { encoding: 'utf8', timeout: 60_000 });
	const line = stdout.split('\n').find(l => l.startsWith('TOOLS:'));
	assert.ok(line, `probe printed no tool list; stdout:\n${stdout.slice(0, 800)}`);
	const names = JSON.parse(line.slice('TOOLS:'.length));
	for (const want of ['summon', 'dismiss', 'join_zoom', 'add_to_vault', 'vision_query']) {
		assert.ok(names.includes(want), `${want} absent under plain node (have ${names.length} tools)`);
	}
	rmSync(dir, { recursive: true, force: true });
});

for (const a of TEST_ARTIFACTS) rmSync(a, { force: true });

console.log();
if (failures.length) {
	console.log('Failures:');
	for (const f of failures) console.log(`  - ${f}`);
	process.exit(1);
}
console.log('All skill-tools-under-plain-node tests passed.');
