// ──────────────────────────────────────────────────────────────────────────────
// Shared types for the solana-service-ts
// ──────────────────────────────────────────────────────────────────────────────

export interface ActionPreview {
  id: string;
  type: string;
  description: string;
  estimatedFee: string;
  params: Record<string, unknown>;
  warnings: string[];
  requiresApproval: boolean;
}

export interface BuildResponse {
  preview: ActionPreview;
  /** Base64-encoded serialised transaction (single-tx actions) */
  transaction?: string;
  /**
   * Ordered list of base64-encoded transactions (multi-tx actions).
   * Must be sent in order — earlier txs are typically oracle/setup steps.
   * When present, `transaction` holds transactions[actionTxIndex] for backwards compat.
   */
  transactions?: string[];
  /** Index into `transactions` that contains the main user action */
  actionTxIndex?: number;
  additionalSignersRequired: number;
  executionSteps?: unknown;
  quote?: unknown;
  isCrossChain: boolean;
  /** Query result data for read-only actions */
  data?: unknown;
}

export interface BuildRequest {
  type: string;
  params: Record<string, unknown>;
}

export interface QuoteRequest {
  inputMint: string;
  outputMint: string;
  amount: string;
  slippageBps?: number;
  onlyDirectRoutes?: boolean;
  swapMode?: string;
  restrictIntermediateTokens?: boolean;
}

export interface SimulateRequest {
  transaction: string;
}

export interface SimulateResponse {
  success: boolean;
  unitsConsumed?: number;
  logs: string[];
  error?: unknown;
  errorMessage?: string;
}

export interface AppError extends Error {
  statusCode: number;
  code: string;
}

export function appError(
  message: string,
  statusCode = 400,
  code = "BAD_REQUEST"
): AppError {
  const err = new Error(message) as AppError;
  err.statusCode = statusCode;
  err.code = code;
  return err;
}

export interface TokenInfo {
  symbol: string;
  mint: string;
  decimals: number;
  name?: string;
  logoURI?: string;
  coingeckoId?: string;
}
