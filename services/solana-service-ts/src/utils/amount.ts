import Decimal from "decimal.js";

Decimal.set({ precision: 40, rounding: Decimal.ROUND_DOWN });

const MAX_U64 = new Decimal("18446744073709551615");

export function parseAmountToBaseUnits(amountStr: string, decimals: number): bigint {
  if (!amountStr || typeof amountStr !== "string") {
    throw new Error("Amount must be a non-empty string");
  }
  let amount: Decimal;
  try {
    amount = new Decimal(amountStr);
  } catch {
    throw new Error(`Invalid amount format: ${amountStr}`);
  }
  if (amount.isNegative() || amount.isZero()) {
    throw new Error("Amount must be positive");
  }
  if (!Number.isInteger(decimals) || decimals < 0 || decimals > 38) {
    throw new Error(`Invalid decimals: ${decimals}`);
  }
  const baseUnits = amount.times(new Decimal(10).pow(decimals));
  if (!baseUnits.isInteger()) {
    throw new Error(`Amount has more decimal places than token decimals (${decimals})`);
  }
  if (baseUnits.greaterThan(MAX_U64)) {
    throw new Error("Amount exceeds maximum representable value");
  }
  return BigInt(baseUnits.toFixed(0));
}
