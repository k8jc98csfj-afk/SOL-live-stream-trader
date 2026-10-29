import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { Router, type IRouter, type Request, type Response } from "express";
import {
  GetBotDashboardResponse,
  GetBotSettingsResponse,
  GetBotSignalsResponse,
  GetBotTradesResponse,
  SetBotRunningBody,
  SetBotRunningResponse,
  UpdateBotSettingsBody,
  UpdateBotSettingsResponse,
} from "@workspace/api-zod";

const router: IRouter = Router();
const enginePath = resolve(process.cwd(), "src/python/sol_engine.py");

type EngineResult = {
  status: number;
  data: unknown;
  diagnostics?: string;
};

function runEngine(
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

async function sendEngineResult(
  req: Request,
  res: Response,
  action: string,
  parseResponse: (data: unknown) => unknown,
  payload: Record<string, unknown> = {},
): Promise<void> {
  try {
    const result = await runEngine(action, payload);
    if (result.diagnostics) {
      req.log.error(
        { action, engineStderr: result.diagnostics },
        "SOL paper engine diagnostics",
      );
    }
    if (result.status >= 400) {
      res.status(result.status).json(result.data);
      return;
    }
    res.json(parseResponse(result.data));
  } catch (error) {
    req.log.error({ err: error, action }, "SOL paper engine request failed");
    res.status(502).json({
      error: "The paper-trading engine is temporarily unavailable.",
    });
  }
}

router.get("/bot/dashboard", async (req, res): Promise<void> => {
  await sendEngineResult(
    req,
    res,
    "dashboard",
    (data) => GetBotDashboardResponse.parse(data),
  );
});

router.get("/bot/signals", async (req, res): Promise<void> => {
  await sendEngineResult(
    req,
    res,
    "signals",
    (data) => GetBotSignalsResponse.parse(data),
  );
});

router.get("/bot/trades", async (req, res): Promise<void> => {
  await sendEngineResult(
    req,
    res,
    "trades",
    (data) => GetBotTradesResponse.parse(data),
  );
});

router.get("/bot/settings", async (req, res): Promise<void> => {
  await sendEngineResult(
    req,
    res,
    "settings",
    (data) => GetBotSettingsResponse.parse(data),
  );
});

router.patch("/bot/settings", async (req, res): Promise<void> => {
  const parsed = UpdateBotSettingsBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }
  await sendEngineResult(
    req,
    res,
    "update_settings",
    (data) => UpdateBotSettingsResponse.parse(data),
    parsed.data,
  );
});

router.post("/bot/control", async (req, res): Promise<void> => {
  const parsed = SetBotRunningBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }
  await sendEngineResult(
    req,
    res,
    "control",
    (data) => SetBotRunningResponse.parse(data),
    parsed.data,
  );
});

export default router;
