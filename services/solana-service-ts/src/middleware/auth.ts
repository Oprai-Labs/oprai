import { Request, Response, NextFunction } from "express";
import { timingSafeEqual } from "crypto";
import { config } from "../config";

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      userWallet?: string;
      userId?: string;
    }
  }
}

function safeCompare(a: string, b: string): boolean {
  try {
    const ba = Buffer.from(a);
    const bb = Buffer.from(b);
    if (ba.length !== bb.length) {
      timingSafeEqual(ba, ba);
      return false;
    }
    return timingSafeEqual(ba, bb);
  } catch {
    return false;
  }
}

/** Validates the X-Internal-Api-Key header injected by the gateway. */
export function internalAuth(req: Request, res: Response, next: NextFunction): void {
  const key = req.headers["x-internal-api-key"];
  if (typeof key !== "string" || !safeCompare(key, config.internalApiKey)) {
    res.status(401).json({ error: "Unauthorized", code: "INVALID_API_KEY" });
    return;
  }
  next();
}

/** Extracts X-User-Wallet and X-User-Id injected by the gateway after JWT validation. */
export function requireWallet(req: Request, res: Response, next: NextFunction): void {
  const wallet = req.headers["x-user-wallet"] as string | undefined;
  if (!wallet) {
    res.status(401).json({ error: "Wallet not provided", code: "NO_WALLET" });
    return;
  }
  req.userWallet = wallet;
  req.userId = (req.headers["x-user-id"] as string | undefined) ?? wallet;
  next();
}
