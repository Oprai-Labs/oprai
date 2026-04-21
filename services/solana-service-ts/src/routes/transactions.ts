import { Router, Request, Response, NextFunction } from "express";
import { internalAuth, requireWallet } from "../middleware/auth";
import {
  createTransaction,
  getTransactionsByWallet,
  getTransactionById,
  updateTransactionStatus,
} from "../db/index";
import { appError } from "../types/index";
import { parsePaginationParams } from "../utils/validation";

const router = Router();
router.use(internalAuth);

// GET /transactions
router.get("/", requireWallet, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const { limit, offset } = parsePaginationParams(req.query as Record<string, unknown>);
    const transactions = await getTransactionsByWallet(req.userWallet!, limit, offset);
    res.json({ transactions });
  } catch (e) {
    next(e);
  }
});

// POST /transactions
router.post("/", requireWallet, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const { action, txHash, protocol, parameters, chain, chatSessionId, chatMessageId } = req.body;
    if (!action) throw appError("action is required");

    const tx = await createTransaction({
      userId: req.userId!,
      userWallet: req.userWallet!,
      chatSessionId,
      chatMessageId,
      txHash,
      chain: chain ?? "solana",
      status: "pending",
      action,
      protocol,
      parameters: parameters ?? {},
    });
    res.status(201).json({ transaction: tx });
  } catch (e) {
    next(e);
  }
});

// GET /transactions/:id
router.get("/:id", requireWallet, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tx = await getTransactionById(req.params.id);
    if (!tx) throw appError("Transaction not found", 404, "NOT_FOUND");
    res.json({ transaction: tx });
  } catch (e) {
    next(e);
  }
});

// PATCH /transactions/:id/status
router.patch("/:id/status", requireWallet, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const { status, txHash, actualFee, errorMessage } = req.body;
    const validStatuses = ["submitted", "confirmed", "failed", "cancelled"];
    if (!validStatuses.includes(status))
      throw appError(`status must be one of: ${validStatuses.join(", ")}`);

    const result = await updateTransactionStatus(req.params.id, status, txHash, actualFee, errorMessage);
    res.json(result);
  } catch (e) {
    next(e);
  }
});

export default router;
