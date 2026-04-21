/**
 * TransactionTrackerService
 *
 * Central TX lifecycle manager:
 *   1. Creates a DB record when TX is submitted (POST /transactions)
 *   2. Waits for confirmation with retry logic (lastValidBlockHeight check)
 *   3. Updates DB on every state transition (PATCH /transactions/{id}/status)
 *   4. Broadcasts status changes via BehaviorSubject
 *
 * Retry strategy:
 *   - If lastValidBlockHeight is exceeded, fetch a new blockhash and retry
 *   - Maximum MAX_RETRIES attempts
 *   - Wait RETRY_DELAY_MS between retries
 */

import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, firstValueFrom } from 'rxjs';
import { ApiService } from './api.service';
import { environment } from '../../../environments/environment';

// ── Constants ────────────────────────────────────────────────────────────────
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 2000;
const CONFIRMATION_COMMITMENT = 'confirmed' as const;

// ── Types ────────────────────────────────────────────────────────────────────

export type TxStatus = 'pending' | 'submitted' | 'confirmed' | 'failed' | 'cancelled';

export interface TrackedTransaction {
  /** Frontend-generated UUID or ID returned from backend */
  id: string;
  /** Solana signature */
  signature: string;
  status: TxStatus;
  /** Transaction type (swap, transfer, stake...) */
  action: string;
  protocol?: string;
  /** Confirmation time (epoch ms) */
  confirmedAt?: number;
  /** Error message */
  error?: string;
  /** Actual fee paid (lamports) */
  actualFee?: string;
}

interface CreateTransactionBody {
  action: string;
  txHash?: string;
  protocol?: string;
  parameters?: Record<string, unknown>;
  chain?: string;
  chatSessionId?: string;
  chatMessageId?: string;
}

interface CreateTransactionResponse {
  transaction: { id: string };
}

@Injectable({ providedIn: 'root' })
export class TransactionTrackerService {
  private readonly api = inject(ApiService);

  /** Status of all active/recent TXs — components can subscribe */
  readonly transactions$ = new BehaviorSubject<Map<string, TrackedTransaction>>(new Map());

  // ── Public API ────────────────────────────────────────────────────────────

  /**
   * Start tracking a signature.
   *
   * @param signature  Solana TX signature (base58)
   * @param action     Transaction type (e.g. 'swap', 'stake')
   * @param options    Optional metadata: protocol, params, chatSessionId, chatMessageId
   * @returns          Transaction ID from DB
   */
  async track(
    signature: string,
    action: string,
    options: {
      protocol?: string;
      params?: Record<string, unknown>;
      chatSessionId?: string;
      chatMessageId?: string;
    } = {}
  ): Promise<string> {
    // 1. Create DB record (in submitted state)
    const txId = await this.createDbRecord(signature, action, options);

    // 2. Update local state
    this.upsert(txId, {
      id: txId,
      signature,
      status: 'submitted',
      action,
      protocol: options.protocol,
    });

    // 3. Start confirmation tracking in the background
    this.waitForConfirmation(txId, signature).catch(() => {});

    return txId;
  }

  /**
   * Mark an existing TX as 'failed' (e.g. user dismissed the wallet).
   */
  async markFailed(txId: string, error: string): Promise<void> {
    this.upsert(txId, { status: 'failed', error });
    await this.updateStatus(txId, 'failed', { errorMessage: error });
  }

  /**
   * Returns the current status of a specific TX.
   */
  getStatus(txId: string): TxStatus | undefined {
    return this.transactions$.value.get(txId)?.status;
  }

  // ── Confirmation Loop ─────────────────────────────────────────────────────

  private async waitForConfirmation(txId: string, signature: string): Promise<void> {
    const { Connection } = await import('@solana/web3.js');
    const connection = new Connection(environment.solanaRpc, CONFIRMATION_COMMITMENT);

    let attempt = 0;

    while (attempt < MAX_RETRIES) {
      try {
        const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash(CONFIRMATION_COMMITMENT);

        const result = await connection.confirmTransaction(
          { signature, blockhash, lastValidBlockHeight },
          CONFIRMATION_COMMITMENT
        );

        if (result.value.err) {
          // On-chain error — TX landed but failed
          const errMsg = JSON.stringify(result.value.err);
          this.upsert(txId, { status: 'failed', error: errMsg });
          await this.updateStatus(txId, 'failed', { errorMessage: errMsg });
          return;
        }

        // Successful confirmation — fetch actual fee
        const actualFee = await this.fetchActualFee(connection, signature);

        this.upsert(txId, {
          status: 'confirmed',
          confirmedAt: Date.now(),
          actualFee,
        });

        await this.updateStatus(txId, 'confirmed', {
          txHash: signature,
          actualFee,
        });

        return;

      } catch (err: unknown) {
        attempt++;
        const errMsg = err instanceof Error ? err.message : String(err);

        const isBlockhashExpired =
          errMsg.includes('block height exceeded') ||
          errMsg.includes('BlockhashNotFound') ||
          errMsg.includes('expired');

        if (isBlockhashExpired && attempt < MAX_RETRIES) {
          // Blockhash expired — retry with a new blockhash
          await this.delay(RETRY_DELAY_MS);
          continue;
        }

        // Unrecoverable error or retry limit reached
        this.upsert(txId, { status: 'failed', error: errMsg });
        await this.updateStatus(txId, 'failed', { errorMessage: errMsg });
        return;
      }
    }
  }

  // ── DB Synchronization ────────────────────────────────────────────────────

  private async createDbRecord(
    signature: string,
    action: string,
    options: {
      protocol?: string;
      params?: Record<string, unknown>;
      chatSessionId?: string;
      chatMessageId?: string;
    }
  ): Promise<string> {
    try {
      const body: CreateTransactionBody = {
        action,
        txHash: signature,
        protocol: options.protocol,
        parameters: options.params,
        chain: 'solana',
        chatSessionId: options.chatSessionId,
        chatMessageId: options.chatMessageId,
      };

      const resp = await firstValueFrom(
        this.api.post<CreateTransactionResponse>('/transactions', body)
      );

      return resp.transaction.id;
    } catch {
      // If DB record creation fails, continue (don't block the TX)
      // Use a temporary client-side ID
      return `local-${Date.now()}-${signature.slice(0, 8)}`;
    }
  }

  private async updateStatus(
    txId: string,
    status: 'submitted' | 'confirmed' | 'failed' | 'cancelled',
    extra: {
      txHash?: string;
      actualFee?: string;
      errorMessage?: string;
    } = {}
  ): Promise<void> {
    if (txId.startsWith('local-')) return; // No record in DB

    try {
      await firstValueFrom(
        this.api.patch(`/transactions/${txId}/status`, {
          status,
          txHash: extra.txHash,
          actualFee: extra.actualFee,
          errorMessage: extra.errorMessage,
        })
      );
    } catch {
      // Don't block the app if status update fails
    }
  }

  // ── Helpers ───────────────────────────────────────────────────────────

  private upsert(id: string, partial: Partial<TrackedTransaction>): void {
    const current = new Map(this.transactions$.value);
    const existing = current.get(id) ?? { id, signature: '', status: 'pending' as TxStatus, action: '' };
    current.set(id, { ...existing, ...partial });
    this.transactions$.next(current);
  }

  private async fetchActualFee(
    connection: import('@solana/web3.js').Connection,
    signature: string
  ): Promise<string | undefined> {
    try {
      const tx = await connection.getTransaction(signature, {
        commitment: 'confirmed',
        maxSupportedTransactionVersion: 0,
      });
      return tx?.meta?.fee?.toString();
    } catch {
      return undefined;
    }
  }

  private delay(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}
