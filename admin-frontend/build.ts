// Production bundle with the same React de-duplication as the dev server (bunfig.toml).
import dedupeReact from "./dedupe-react";

const out = await Bun.build({
  entrypoints: ["./src/index.html"],
  outdir: "./dist",
  minify: true,
  plugins: [dedupeReact],
});
if (!out.success) {
  for (const log of out.logs) console.error(log);
  process.exit(1);
}
console.log(`admin-frontend: ${out.outputs.length} files in dist/`);
