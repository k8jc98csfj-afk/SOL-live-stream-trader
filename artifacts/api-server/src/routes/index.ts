import { Router, type IRouter } from "express";
import healthRouter from "./health";
import solScalperRouter from "./sol-scalper";

const router: IRouter = Router();

router.use(healthRouter);
router.use(solScalperRouter);

export default router;
