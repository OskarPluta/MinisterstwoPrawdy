// One React for the whole bundle. The admin app imports the design system from ../frontend/src, whose
// bare `react` imports would otherwise resolve to ../frontend/node_modules: two Reacts, broken hooks.
// Used by the dev server (bunfig.toml [serve.static]) and by build.ts.
import type { BunPlugin } from "bun";

const plugin: BunPlugin = {
  name: "dedupe-react",
  setup(build) {
    build.onResolve({ filter: /^react(-dom)?(\/.*)?$/ }, (args) => ({
      path: Bun.resolveSync(args.path, import.meta.dir),
    }));
  },
};

export default plugin;
