import { Router } from "express";
import { getPool } from "../db/index";

const router = Router();

router.get("/health", async (_req, res) => {
  let dbOk = false;
  try {
    await getPool().query("SELECT 1");
    dbOk = true;
  } catch {
    // DB not required for health to pass startup
  }

  res.json({
    status: "ok",
    service: "solana-service-ts",
    timestamp: new Date().toISOString(),
    database: dbOk ? "ok" : "unavailable",
  });
});

export default router;
