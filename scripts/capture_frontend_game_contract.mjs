// Read-only consumer capture: node scripts/capture_frontend_game_contract.mjs WEB_ROOT
// Reads a catalog from stdin and prints the real frontend's request/export bodies.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";

if (!process.argv[2]) throw new Error("Pass the tanbunism-web repository path");
const root = resolve(process.argv[2]);
const requireWeb = createRequire(resolve(root, "package.json"));
const { build } = requireWeb("esbuild");
const source = JSON.parse(readFileSync(0, "utf8"));
const input = source.catalog ?? source;
const result = await build({
  stdin: {
    contents: `
      import { prepareSkillCatalog, requestGameCatalog } from './app/features/game/catalog';
      import { skillFileText } from './app/features/admin/game/skillFiles';
      export async function capture(input) {
        const catalog = prepareSkillCatalog(input);
        await requestGameCatalog(true, catalog);
        return JSON.parse(skillFileText(catalog.skills, catalog.skill_templates,
          catalog.elements, catalog.variant_sets));
      }
    `,
    resolveDir: root,
    loader: "ts",
  },
  bundle: true,
  platform: "node",
  format: "cjs",
  write: false,
  alias: { "~": resolve(root, "app") },
  define: { "import.meta.env.VITE_API_BASE_URL": '"https://contract.invalid"' },
});
const module = { exports: {} };
let catalogBody;
const fetch = async (url, init) => {
  if (url !== "https://contract.invalid/admin/game/catalog" || init.method !== "PUT") {
    throw new Error("Unexpected request in contract capture");
  }
  catalogBody = JSON.parse(init.body);
  return { ok: true, json: async () => catalogBody };
};
runInNewContext(result.outputFiles[0].text, {
  module, exports: module.exports, fetch, crypto: globalThis.crypto,
});
const skillFile = await module.exports.capture(input);
process.stdout.write(`${JSON.stringify({ catalog: catalogBody, skill_file: skillFile }, null, 2)}\n`);
