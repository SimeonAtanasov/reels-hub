// Applies the migrations to every D1 database in wrangler.toml: the inbox (DB, migrations/) and each person's
// online library (LIB_<PERSON>, migrations-lib/). Usage: node scripts/migrate-all.mjs --remote | --local
// It runs wrangler without its "continue?" question (CI=true): on Windows that question was sometimes answered "no"
// by itself for the first database that needed it. You started the command, so the answer is yes.
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const where = process.argv.includes('--local') ? '--local' : '--remote';
const toml = readFileSync(new URL('../wrangler.toml', import.meta.url), 'utf8');
const bindings = [...toml.matchAll(/^\s*binding\s*=\s*"([^"]+)"/gm)].map((m) => m[1]);
if (!bindings.length) {
  console.error('No [[d1_databases]] in wrangler.toml');
  process.exit(1);
}
for (const b of bindings) {
  console.log(`\n== ${b}`);
  if (!/^[A-Za-z0-9_]+$/.test(b)) {
    console.error(`Skipped ${b}: a binding name has only letters, digits and _`);
    continue;
  }
  // one command line (npx is a .cmd file on Windows, which needs the shell); the binding was checked just above
  const r = spawnSync(`npx wrangler d1 migrations apply ${b} ${where}`, { stdio: 'inherit', shell: true, env: { ...process.env, CI: 'true' } });
  if (r.status !== 0) {
    console.error(`\nStopped at ${b}. Fix the message above (often: the database_id in wrangler.toml), then run it again.`);
    process.exit(r.status || 1);
  }
}
console.log('\nDone: each database above says "No migrations to apply" or lists its new migrations with a check mark.');
