import { Router, type IRouter, type Request, type Response } from "express";
import { runSolEngine } from "../lib/sol-scalper-engine";
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

async function sendEngineResult(
  req: Request,
  res: Response,
  action: string,
  parseResponse: (data: unknown) => unknown,
  payload: Record<string, unknown> = {},
): Promise<void> {
  try {
    const result = await runSolEngine(action, payload);
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
