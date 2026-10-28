const path = require("node:path");
const fs = require("node:fs");
const esbuild = require(process.env.ECF_ESBUILD_MODULE || "esbuild");
const source = path.join(__dirname, "source");
const result = esbuild.buildSync({
  absWorkingDir: source, entryPoints: [path.join(source, "verify_log.mjs")],
  bundle: true, platform: "node", format: "esm", metafile: true,
  nodePaths: [path.join(source, "deps")],
  outfile: path.join(__dirname, "verify_log.bundle.mjs"),
});
fs.mkdirSync(path.join(__dirname, ".build"), {recursive: true});
fs.writeFileSync(path.join(__dirname, ".build/metafile.json"), JSON.stringify(result.metafile, null, 2));
console.log("Official parser bundle rebuilt");
