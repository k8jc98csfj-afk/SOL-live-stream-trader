import { logger } from "./logger";
import { runSolEngine } from "./sol-scalper-engine";

let timer: NodeJS.Timeout | undefined;
let evaluationInFlight = false;

async function evaluateOnce(): Promise<void> {
  if (evaluationInFlight) return;
  evaluationInFlight = true;
  try {
    const result = await runSolEngine("evaluate");
    if (result.status >= 400) {
      logger.warn(
        { status: result.status, result: result.data, diagnostics: result.diagnostics },
        "SOL paper evaluation did not complete",
      );
    }
  } catch (error) {
    logger.error({ err: error }, "SOL paper evaluation worker failed");
  } finally {
    evaluationInFlight = false;
  }
}

export async function startSolScalperWorker(): Promise<void> {
  if (timer) return;

  const initialized = await runSolEngine("startup");
  if (initialized.status >= 400) {
    throw new Error("Could not initialize the SOL paper engine in the OFF state.");
  }
  logger.info("SOL paper simulator initialized OFF");

  const requestedInterval = Number(process.env.SOL_SCALPER_INTERVAL_MS ?? 5000);
  const intervalMs =
    Number.isFinite(requestedInterval) && requestedInterval >= 1000
      ? requestedInterval
      : 5000;

  void evaluateOnce();
  timer = setInterval(() => {
    void evaluateOnce();
  }, intervalMs);
  timer.unref();
  logger.info({ intervalMs }, "SOL paper evaluation worker started");
}
