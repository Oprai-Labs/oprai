import { Router, Request, Response, NextFunction } from "express";
import { internalAuth, requireWallet } from "../middleware/auth";
import { buildAction, validateAction } from "../services/builder";
import { getQuote } from "../services/jupiter";
import { getDcaOrders } from "../services/dca";
import { getLimitOrders } from "../services/limit_order";
import { appError } from "../types/index";
import { resolveToken } from "../services/tokens";
import { parseAmountToBaseUnits } from "../utils/amount";
import { parseLimitOrderStatus } from "../utils/validation";

const router = Router();

// All /actions routes require internal API key
router.use(internalAuth);

// ── POST /actions/quote ────────────────────────────────────────────────────
router.post("/quote", requireWallet, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const { inputMint, outputMint, amount, slippageBps, onlyDirectRoutes, swapMode, restrictIntermediateTokens } = req.body;
    if (!inputMint || !outputMint || !amount)
      throw appError("inputMint, outputMint, amount are required");

    const inToken = resolveToken(inputMint);
    const decimals = inToken?.decimals ?? 9;
    const amountStr = String(amount);
    const amountLamports = parseAmountToBaseUnits(amountStr, decimals).toString();

    const quote = await getQuote({
      inputMint: inToken?.mint ?? inputMint,
      outputMint: resolveToken(outputMint)?.mint ?? outputMint,
      amount: amountLamports,
      slippageBps,
      onlyDirectRoutes,
      swapMode,
      restrictIntermediateTokens,
    });

    res.json({ quote });
  } catch (e) {
    next(e);
  }
});

// ── POST /actions/build ────────────────────────────────────────────────────
router.post("/build", requireWallet, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const { type, params } = req.body;
    validateAction(type, params ?? {});
    const result = await buildAction({ type, params: params ?? {} }, req.userWallet!);
    res.json(result);
  } catch (e) {
    next(e);
  }
});

// ── POST /actions/simulate ─────────────────────────────────────────────────
router.post("/simulate", requireWallet, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const { transaction } = req.body;
    if (!transaction) throw appError("transaction is required");

    const { Connection } = await import("@solana/web3.js");
    const { config } = await import("../config.js");
    const { default: bs58 } = await import("bs58");

    const connection = new Connection(config.solanaRpc, "confirmed");
    const txBytes = Buffer.from(transaction, "base64");

    // Try as VersionedTransaction first, then legacy
    let result;
    try {
      const { VersionedTransaction } = await import("@solana/web3.js");
      const vtx = VersionedTransaction.deserialize(txBytes);
      result = await connection.simulateTransaction(vtx, { sigVerify: false });
    } catch {
      const { Transaction } = await import("@solana/web3.js");
      const tx = Transaction.from(txBytes);
      result = await connection.simulateTransaction(tx, undefined, undefined);
    }

    res.json({
      success: !result.value.err,
      unitsConsumed: result.value.unitsConsumed,
      logs: result.value.logs ?? [],
      error: result.value.err ?? null,
      errorMessage: result.value.err ? JSON.stringify(result.value.err) : null,
    });
  } catch (e) {
    next(e);
  }
});

// ── GET /actions/limit-orders ──────────────────────────────────────────────
router.get("/limit-orders", requireWallet, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const status = parseLimitOrderStatus(req.query.status);
    const result = await getLimitOrders({ status }, req.userWallet!);
    res.json(result.data ?? []);
  } catch (e) {
    next(e);
  }
});

// ── GET /actions/dca-orders ────────────────────────────────────────────────
router.get("/dca-orders", requireWallet, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const status = parseLimitOrderStatus(req.query.status);
    const result = await getDcaOrders({ status }, req.userWallet!);
    res.json(result.data ?? []);
  } catch (e) {
    next(e);
  }
});

export default router;
