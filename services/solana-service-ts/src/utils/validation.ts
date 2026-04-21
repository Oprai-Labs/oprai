export function parsePaginationParams(query: Record<string, unknown>): { limit: number; offset: number } {
  const rawLimit = parseInt(String(query.limit ?? "50"), 10);
  const rawOffset = parseInt(String(query.offset ?? "0"), 10);
  if (!Number.isFinite(rawLimit) || !Number.isFinite(rawOffset)) {
    throw new Error("Invalid pagination parameters");
  }
  return {
    limit: Math.min(Math.max(1, rawLimit), 100),
    offset: Math.max(0, Math.min(rawOffset, 1_000_000)),
  };
}

const VALID_LIMIT_ORDER_STATUSES = ["open", "closed", "cancelled"] as const;
type LimitOrderStatus = (typeof VALID_LIMIT_ORDER_STATUSES)[number];

export function parseLimitOrderStatus(raw: unknown): LimitOrderStatus {
  if (typeof raw === "string" && VALID_LIMIT_ORDER_STATUSES.includes(raw as LimitOrderStatus)) {
    return raw as LimitOrderStatus;
  }
  return "open";
}
