import express from "express";
import cors from "cors";
import helmet from "helmet";
import rateLimit from "express-rate-limit";
import { config } from "./config";
import healthRouter from "./routes/health";
import actionsRouter from "./routes/actions";
import protocolsRouter from "./routes/protocols";
import transactionsRouter from "./routes/transactions";
import { errorHandler, notFound } from "./middleware/error";

const app = express();

// ── Global middleware ──────────────────────────────────────────────────────
app.use(helmet());
app.use(cors({ origin: config.corsOrigin, credentials: true }));
app.use(express.json({ limit: "2mb" }));

// SEC-021: 100 req/min per IP with standard rate-limit headers
app.use(
  rateLimit({
    windowMs: 60_000,
    max: 100,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: "Too many requests, please try again later." },
  }),
);

// ── Routes ─────────────────────────────────────────────────────────────────
app.use(healthRouter);
app.use("/actions", actionsRouter);
app.use(protocolsRouter);
app.use("/transactions", transactionsRouter);

// ── 404 / Error handlers ──────────────────────────────────────────────────
app.use(notFound);
app.use(errorHandler);

// ── Start ──────────────────────────────────────────────────────────────────
app.listen(config.port, () => {
  console.log(`[solana-service-ts] listening on port ${config.port}`);
  console.log(`[solana-service-ts] RPC: ${config.solanaRpc}`);
  console.log(`[solana-service-ts] env: ${config.nodeEnv}`);
});

export default app;
