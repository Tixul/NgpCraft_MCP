import { loadTranspiler } from "./_transpiler_loader.js";

export const definition = {
  name: "ngpc_lint",
  description:
    "Run the NgpCraft hardware-fidelity lint on a C source string. Catches NGP_FAR missing on ROM data (HW-1), volatile missing on ISR-shared globals (HW-2), C99 for-decl that the cc900 compiler rejects (HW-3b). Each error explains the hardware symptom + a concrete fix. Returns either {ok:true} or {ok:false, errors:[…]}.",
  inputSchema: {
    type: "object",
    properties: {
      code: {
        type: "string",
        description: "C source code to lint (single file).",
      },
      filename: {
        type: "string",
        default: "main.c",
        description: "Logical filename for error reporting only.",
      },
    },
    required: ["code"],
  },
};

export async function handler({ code, filename = "main.c" }) {
  const { Interp } = await loadTranspiler();
  try {
    Interp.compile(code);
    return { ok: true, filename, code_bytes: code.length };
  } catch (err) {
    if (err.name === "HwFidelityError") {
      return {
        ok: false,
        filename,
        kind: "hardware_fidelity",
        error_count: err.hwErrors.length,
        errors: err.hwErrors,
        formatted: err.message,
      };
    }
    return {
      ok: false,
      filename,
      kind: "transpile_error",
      message: err.message,
    };
  }
}
