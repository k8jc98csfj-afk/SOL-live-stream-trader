import { spawn } from "node:child_process";
import { resolve } from "node:path";

export type EngineResult = {
  status: number;
  data: unknown;
  diagnostics?: string;
};

const enginePath = resolve(process.cwd(), "src/python/sol_engine.py");

export function runSolEngine(
  action: string,
  payload: Record<string, unknown> = {},
): Promise<EngineResult> {
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(
      process.env.SOL_SCALPER_PYTHON ?? "python3",
      [enginePath, action],
      {
        cwd: process.cwd(),
        env: process.env,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    let stdout = "";
    let stderr = "";
    const timeout = setTimeout(() => {
      child.kill("SIGKILL");
      rejectPromise(new Error("Python paper engine timed out."));
    }, 25_000);

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
    });
    child.once("error", (error) => {
      clearTimeout(timeout);
      rejectPromise(error);
    });
    child.once("close", (code) => {
      clearTimeout(timeout);
      try {
        const data: unknown = JSON.parse(stdout);
        const status =
          typeof data === "object" &&
          data !== null &&
          "status" in data &&
          typeof data.status === "number"
            ? data.status
            : code === 0
              ? 200
              : 500;
        resolvePromise({ status, data, diagnostics: stderr || undefined });
      } catch {
        rejectPromise(
          new Error(
            `Python paper engine returned invalid output (exit ${code ?? "unknown"}).`,
          ),
        );
      }
    });
    child.stdin.end(JSON.stringify(payload));
  });
}
