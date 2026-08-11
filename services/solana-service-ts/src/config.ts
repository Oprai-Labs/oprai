import { config as dotenvConfig } from "dotenv";
import { resolve } from "path";

// Load root .env
dotenvConfig({ path: resolve(__dirname, "../../../.env") });

function required(key: string): string {
  const val = process.env[key];
  if (!val) throw new Error(`Required env var ${key} is not set`);
  return val;
}

function optional(key: string, fallback: string): string {
  return process.env[key] || fallback;
}

export const config = {
  port: parseInt(optional("PORT", "3031"), 10),
  nodeEnv: optional("NODE_ENV", "development"),

  // Auth
  internalApiKey: required("OPRAI_INTERNAL_API_KEY"),
  corsOrigin: optional("CORS_ORIGIN", "http://localhost:3000"),

  // Database
  databaseUrl: optional(
    "DATABASE_URL",
    `postgresql://${optional("DB_SUPERUSER", "oprai")}:${optional("DB_SUPERPASS", "oprai")}@localhost:5433/${optional("DB_SUPERDB", "oprai")}`
  ),

  // Solana
  solanaRpc: optional(
    "SOLANA_RPC",
    "https://api.mainnet-beta.solana.com"
  ),

  // API keys
  jupiterApiKey: optional("JUPITER_API_KEY", ""),
  heliusApiKey: optional("HELIUS_API_KEY", ""),
};
