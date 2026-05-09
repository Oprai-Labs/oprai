import { Pool, PoolClient } from "pg";
import { config } from "../config";

let pool: Pool | null = null;

export function getPool(): Pool {
  if (!pool) {
    pool = new Pool({ connectionString: config.databaseUrl });
    pool.on("error", (err) => {
      console.error("[db] unexpected pool error:", err.message);
    });
  }
  return pool;
}

export async function query<T extends Record<string, unknown> = Record<string, unknown>>(
  sql: string,
  params?: unknown[]
): Promise<T[]> {
  const client = getPool();
  const res = await client.query<T>(sql, params);
  return res.rows;
}

export async function withClient<T>(
  fn: (client: PoolClient) => Promise<T>
): Promise<T> {
  const client = await getPool().connect();
  try {
    return await fn(client);
  } finally {
    client.release();
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Transaction record operations
// ──────────────────────────────────────────────────────────────────────────────

export interface TxRecord {
  id: string;
  userId: string;
  userWallet: string;
  chatSessionId?: string;
  chatMessageId?: string;
  txHash?: string;
  chain: string;
  status: string;
  action: string;
  protocol?: string;
  parameters: Record<string, unknown>;
  estimatedFee?: string;
  actualFee?: string;
  errorMessage?: string;
  createdAt: Date;
  updatedAt: Date;
}

export async function createTransaction(
  data: Omit<TxRecord, "id" | "createdAt" | "updatedAt">
): Promise<TxRecord> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const rows = await query<any>(
    `INSERT INTO solana_schema.transactions
       (user_id, user_wallet, chat_session_id, chat_message_id, tx_hash,
        chain, status, action, protocol, parameters, estimated_fee,
        actual_fee, error_message)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
     RETURNING *`,
    [
      data.userId,
      data.userWallet,
      data.chatSessionId ?? null,
      data.chatMessageId ?? null,
      data.txHash ?? null,
      data.chain,
      data.status,
      data.action,
      data.protocol ?? null,
      JSON.stringify(data.parameters),
      data.estimatedFee ?? null,
      data.actualFee ?? null,
      data.errorMessage ?? null,
    ]
  );
  return rows[0];
}

export async function getTransactionsByWallet(
  userWallet: string,
  limit = 50,
  offset = 0
): Promise<TxRecord[]> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return query<any>(
    `SELECT * FROM solana_schema.transactions
     WHERE user_wallet = $1
     ORDER BY created_at DESC
     LIMIT $2 OFFSET $3`,
    [userWallet, Math.min(limit, 100), offset]
  );
}

export async function getTransactionById(id: string): Promise<TxRecord | null> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const rows = await query<any>(
    `SELECT * FROM solana_schema.transactions WHERE id = $1`,
    [id]
  );
  return rows[0] ?? null;
}

export async function updateTransactionStatus(
  id: string,
  status: string,
  txHash?: string,
  actualFee?: string,
  errorMessage?: string
): Promise<{ id: string; status: string; updatedAt: Date }> {
  const rows = await query<{ id: string; status: string; updatedAt: Date }>(
    `UPDATE solana_schema.transactions
     SET status = $2,
         tx_hash = COALESCE($3, tx_hash),
         actual_fee = COALESCE($4, actual_fee),
         error_message = COALESCE($5, error_message),
         submitted_at = CASE WHEN $2 = 'submitted' THEN NOW() ELSE submitted_at END,
         confirmed_at = CASE WHEN $2 IN ('confirmed','completed') THEN NOW() ELSE confirmed_at END
     WHERE id = $1
     RETURNING id, status, updated_at AS "updatedAt"`,
    [id, status, txHash ?? null, actualFee ?? null, errorMessage ?? null]
  );
  if (!rows[0]) throw new Error(`Transaction ${id} not found`);
  return rows[0];
}
