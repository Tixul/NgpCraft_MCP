import { readdir, mkdir, copyFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const TEMPLATES_ROOT = join(__dirname, "..", "..", "vendor", "templates");

async function listTemplates() {
  const entries = await readdir(TEMPLATES_ROOT, { withFileTypes: true });
  return entries.filter((e) => e.isDirectory()).map((e) => e.name);
}

async function copyRecursive(src, dst) {
  const entries = await readdir(src, { withFileTypes: true });
  await mkdir(dst, { recursive: true });
  for (const e of entries) {
    const s = join(src, e.name);
    const d = join(dst, e.name);
    if (e.isDirectory()) await copyRecursive(s, d);
    else if (e.isFile()) await copyFile(s, d);
  }
}

export const definition = {
  name: "ngpc_new_project",
  description:
    "Scaffold a new NGPC project by copying one of the genre templates (base, cavegen, platformer, racer). Target directory must not already exist (safety). Returns the list of files copied.",
  inputSchema: {
    type: "object",
    properties: {
      template: {
        type: "string",
        enum: ["base", "cavegen", "platformer", "racer"],
      },
      target_dir: {
        type: "string",
        description: "Absolute path where the new project will be created",
      },
    },
    required: ["template", "target_dir"],
  },
};

export async function handler({ template, target_dir }) {
  const templates = await listTemplates();
  if (!templates.includes(template)) {
    return {
      error: `Unknown template '${template}'`,
      available: templates,
    };
  }
  const src = join(TEMPLATES_ROOT, template);
  const dst = resolve(target_dir);
  try {
    await stat(dst);
    return { error: `Target already exists: ${dst}. Aborting to avoid overwrite.` };
  } catch {
    // not exist — good
  }
  await copyRecursive(src, dst);
  return {
    template,
    target: dst,
    status: "ok",
    next_steps: [
      "Open target_dir in your editor",
      "Review README.md + ROADMAP.md in the template",
      "Build: make (requires NgpCraft toolchain installed separately)",
      "Lint code with ngpc_lint before each build",
    ],
  };
}
