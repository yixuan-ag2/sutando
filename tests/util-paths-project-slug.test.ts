import { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, utimesSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { projectSlug, slugDerivationKey, slugFormula } from '../src/util_paths.js';

/**
 * TypeScript twin of tests/util-paths-project-slug.test.py — keep both in step.
 *
 * Regression guard for sonichi#2723: three inline slug formulas, each mapping
 * only `/`, silently named directories Claude Code never writes to on any
 * install whose path contains a space or a dot (every macOS app-bundle install
 * under `~/Library/Application Support/`). Nothing errored — the wrong dir was
 * created on first write and memory split across parallel stores.
 */

const BUNDLE = '/Users/me/Library/Application Support/space.ag2.app/engine/sutando';
const BUNDLE_TRUE_SLUG = '-Users-me-Library-Application-Support-space-ag2-app-engine-sutando';

/** What the pre-fix code computed, at all three call sites. */
const legacyFormula = (path: string) => path.replace(/\//g, '-');

describe('slugDerivationKey', () => {
	it('gives one key for the divergent derivations of a single path', () => {
		const variants = [
			BUNDLE_TRUE_SLUG,
			'-Users-me-Library-Application Support-space.ag2.app-engine-sutando',
			'-Users-me-Library-Application-Support-space.ag2.app-engine-sutando',
		];
		const keys = new Set(variants.map(slugDerivationKey));
		assert.equal(keys.size, 1, `variants disagreed: ${[...keys]}`);
	});

	it('does not collide unrelated projects', () => {
		assert.notEqual(
			slugDerivationKey(BUNDLE_TRUE_SLUG),
			slugDerivationKey('-Users-me-Documents-unrelated-repo'),
		);
	});
});

describe('slugFormula (fallback only)', () => {
	it('pins the bug: `/`-only mapping named a dir that never exists', () => {
		assert.notEqual(legacyFormula(BUNDLE), BUNDLE_TRUE_SLUG);
	});

	it('reproduces the bundle slug', () => {
		assert.equal(slugFormula(BUNDLE), BUNDLE_TRUE_SLUG);
	});

	it('is unchanged for plain paths — strict superset of the old behavior', () => {
		const plain = '/Users/me/workspace/sutando';
		assert.equal(slugFormula(plain), legacyFormula(plain));
	});
});

describe('projectSlug — discovery', () => {
	let home: string;
	let prev: string | undefined;

	beforeEach(() => {
		home = mkdtempSync(join(tmpdir(), 'sutando-project-slug-'));
		mkdirSync(join(home, 'projects'));
		prev = process.env.CLAUDE_CONFIG_DIR;
		process.env.CLAUDE_CONFIG_DIR = home;
	});

	afterEach(() => {
		if (prev === undefined) delete process.env.CLAUDE_CONFIG_DIR;
		else process.env.CLAUDE_CONFIG_DIR = prev;
		try { rmSync(home, { recursive: true, force: true }); } catch {}
	});

	const mkproject = (name: string) => {
		const d = join(home, 'projects', name);
		mkdirSync(d);
		return d;
	};

	it('finds the real dir even when the formula cannot reproduce it', () => {
		// The case that makes discovery worth the code: Claude Code has already
		// changed how it maps `_`, so any formula is a prediction that expires.
		const real = '-Users-me-Library-Application_Support-space-ag2-app-engine-sutando';
		mkproject(real);
		assert.notEqual(slugFormula(BUNDLE), real);
		assert.equal(projectSlug(BUNDLE), real);
	});

	it('falls back to the formula when nothing exists yet', () => {
		assert.equal(projectSlug(BUNDLE), slugFormula(BUNDLE));
	});

	it('ignores unrelated projects', () => {
		mkproject('-Users-me-Documents-unrelated-repo');
		assert.equal(projectSlug(BUNDLE), slugFormula(BUNDLE));
	});

	it('prefers the most recently modified on a split', () => {
		const stale = mkproject('-Users-me-Library-Application Support-space.ag2.app-engine-sutando');
		const live = mkproject(BUNDLE_TRUE_SLUG);
		utimesSync(stale, 1_000_000, 1_000_000);
		utimesSync(live, 2_000_000, 2_000_000);
		assert.equal(projectSlug(BUNDLE), BUNDLE_TRUE_SLUG);
	});

	it('treats a missing projects dir as "not yet created", not an error', () => {
		rmSync(join(home, 'projects'), { recursive: true, force: true });
		assert.equal(projectSlug(BUNDLE), slugFormula(BUNDLE));
	});
});
