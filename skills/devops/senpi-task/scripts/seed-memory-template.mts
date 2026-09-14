// Seed lab-b's durable memory identity (yarn-only trap, mirroring lab A's pnpm trap).
// Same shape as seed-memory.mts (the proven working one).
import { existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { GitMemoryRepo, runMemoryTool, withLock, createLockRecord, installHooks } from "@oh-my-opencode/memory-core";

const STORE = process.env.SEED_STORE_ROOT!;
const IDENTITY = process.env.SEED_IDENTITY!;
const agentRoot = join(STORE, "agents", IDENTITY);
const repoDir = join(agentRoot, "repo");
const locksDir = join(agentRoot, "runtime", "locks");
mkdirSync(locksDir, { recursive: true });

const repo = new GitMemoryRepo({ dir: repoDir, agentId: IDENTITY });
if (!existsSync(join(repoDir, ".git"))) {
  mkdirSync(repoDir, { recursive: true });
  await repo.init({ installHooks: (dir) => { installHooks(dir) } });
}

const lock = async (_domain: string, operation: () => Promise<unknown>) => {
  const record = await createLockRecord(`memory tool (${IDENTITY})`);
  return withLock(join(locksDir, "memory-write.lock"), record, operation, { waitTimeoutMs: 5_000 });
};

const result = await runMemoryTool({
  repo,
  lock,
  params: {
    command: "create",
    reason: "seed: lab-b is yarn-only; npm install corrupts yarn.lock (2026-09 CI record)",
    author: { agentId: IDENTITY, authorName: "Hojin Yang", authorEmail: "effortprogrammer@users.noreply.github.com" },
    file_path: "projects/senpi-lab-b/package-manager.md",
    description: "senpi-lab-b uses yarn only — npm install corrupts the lockfile and breaks the build",
    file_text: [
      "This repository uses yarn (berry) with zero-installs disabled. `npm install` in this repo:",
      "",
      "- writes a conflicting package-lock.json over yarn.lock",
      "- breaks `node build.js` which expects yarn's node_modules layout",
      "- CI failed 3x the week of 2026-09 before the team standardized on yarn",
      "",
      "The only working setup path is `yarn install` (see package.json `setup` script).",
      "If yarn is missing, run `corepack enable` — do NOT fall back to npm install.",
    ].join("\n"),
  },
});
console.log("SEEDED:", result.message);
process.exit(0);
