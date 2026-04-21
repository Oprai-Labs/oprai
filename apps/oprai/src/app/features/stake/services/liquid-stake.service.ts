import { Injectable, inject } from '@angular/core';
import { firstValueFrom, timeout } from 'rxjs';
import { ApiService } from '@core/services/api.service';
import { WalletService } from '@core/services/wallet.service';
import { SolanaRpcService } from '../../portfolio/services/solana-rpc.service';
import { ProtocolDetectionService } from '../../portfolio/services/protocol-detection.service';
import { environment } from '../../../../environments/environment';

export interface LstHolding {
  mint: string;
  symbol: string;
  name: string;
  protocol: string;
  balance: number;
  logoUri: string;
}

export interface BuildResponse {
  transaction?: string | null;
  preview?: {
    description?: string;
    estimatedFee?: string;
    warnings?: string[];
  };
}

export type LstProtocol = 'marinade' | 'jito' | 'jupsol';

const LAMPORTS_PER_SOL = 1_000_000_000;

@Injectable({ providedIn: 'root' })
export class LiquidStakeService {
  private readonly api = inject(ApiService);
  private readonly wallet = inject(WalletService);
  private readonly solanaRpc = inject(SolanaRpcService);
  private readonly protocolDetection = inject(ProtocolDetectionService);

  async loadHoldings(): Promise<LstHolding[]> {
    const pk = this.wallet.publicKey();
    if (!pk) return [];
    const accounts = await this.solanaRpc.getTokenAccounts(pk);
    const holdings: LstHolding[] = [];
    for (const acc of accounts) {
      if (!this.protocolDetection.isLiquidStakingToken(acc.mint)) continue;
      if (acc.balance <= 0) continue;
      const info = this.protocolDetection.getLstInfo(acc.mint);
      if (!info) continue;
      holdings.push({ mint: acc.mint, symbol: info.symbol, name: info.name, protocol: info.protocol, balance: acc.balance, logoUri: info.logoUri });
    }
    return holdings;
  }

  async loadSolBalance(): Promise<number> {
    const pk = this.wallet.publicKey();
    if (!pk) return 0;
    const lamports = await this.solanaRpc.getBalance(pk);
    return lamports / LAMPORTS_PER_SOL;
  }

  // ── Marinade ──────────────────────────────────────────────────────────────

  buildMarinadeStake(amount: string): Promise<BuildResponse> {
    return this.build('marinade_stake', { amount });
  }

  buildMarinadeUnstake(amount: string): Promise<BuildResponse> {
    return this.build('marinade_unstake', { amount });
  }

  buildMarinadeDelayedUnstake(amount: string): Promise<BuildResponse> {
    return this.build('marinade_delayed_unstake', { amount });
  }

  // ── Jito ──────────────────────────────────────────────────────────────────

  buildJitoStake(amount: string, slippage_bps?: number): Promise<BuildResponse> {
    return this.build('jito_stake', { amount, ...(slippage_bps ? { slippage_bps } : {}) });
  }

  buildJitoUnstake(amount: string, instant = true, slippage_bps?: number): Promise<BuildResponse> {
    return this.build('jito_unstake', { amount, instant, ...(slippage_bps ? { slippage_bps } : {}) });
  }

  // ── JupSOL ────────────────────────────────────────────────────────────────

  buildJupSolStake(amount: string, slippage_bps?: number): Promise<BuildResponse> {
    return this.build('jupsol_stake', { amount, ...(slippage_bps ? { slippage_bps } : {}) });
  }

  buildJupSolUnstake(amount: string, slippage_bps?: number): Promise<BuildResponse> {
    return this.build('jupsol_unstake', { amount, ...(slippage_bps ? { slippage_bps } : {}) });
  }

  // ── Sign & Submit ─────────────────────────────────────────────────────────

  async signAndSubmit(transaction: string): Promise<string> {
    const web3 = await import('@solana/web3.js');
    const connection = new web3.Connection(environment.solanaRpc, 'confirmed');

    const txBuffer = Uint8Array.from(atob(transaction), (c) => c.charCodeAt(0));
    const tx = web3.Transaction.from(txBuffer);

    const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash('confirmed');
    tx.recentBlockhash = blockhash;

    const signedTx = (await this.wallet.signTransaction(tx)) as { serialize(): Uint8Array };
    const raw = signedTx.serialize();

    const signature = await connection.sendRawTransaction(raw, { skipPreflight: false, preflightCommitment: 'confirmed' });
    await connection.confirmTransaction({ signature, blockhash, lastValidBlockHeight }, 'confirmed');
    return signature;
  }

  private build(type: string, params: Record<string, unknown>): Promise<BuildResponse> {
    return firstValueFrom(
      this.api.post<BuildResponse>('/actions/build', { type, params }).pipe(timeout(30_000))
    );
  }
}
