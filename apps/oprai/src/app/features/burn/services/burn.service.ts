import { Injectable, inject } from '@angular/core';
import { firstValueFrom, timeout } from 'rxjs';
import { ApiService } from '@core/services/api.service';
import { WalletService } from '@core/services/wallet.service';
import { SolanaRpcService } from '../../portfolio/services/solana-rpc.service';
import { createSolanaConnection } from '@core/utils/solana-connection';

export interface TokenAccountInfo {
  pubkey: string;
  mint: string;
  symbol: string;
  balance: number;
  balanceRaw: string;
  decimals: number;
  rentLamports: number;
  logoUri?: string;
  selected: boolean;
}

interface BuildResponse {
  transaction?: string | null;
  preview?: {
    id?: string;
    description?: string;
    estimatedFee?: string;
    estimatedRefund?: string;
    warnings?: string[];
    requiresApproval?: boolean;
  };
}

const LAMPORTS_PER_SOL = 1_000_000_000;

// Well-known token symbols for display
const TOKEN_SYMBOLS: Record<string, { symbol: string; logoUri?: string }> = {
  EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v: { symbol: 'USDC', logoUri: '/assets/coins/usdc.svg' },
  Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB: { symbol: 'USDT', logoUri: '/assets/coins/usdt.svg' },
  DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263: { symbol: 'BONK' },
  EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm: { symbol: 'WIF' },
  JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN: { symbol: 'JUP' },
  mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So: { symbol: 'mSOL' },
  J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn: { symbol: 'jitoSOL' },
  bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1: { symbol: 'bSOL' },
  '27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4': { symbol: 'JLP' },
  HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3: { symbol: 'PYTH' },
  jtojtomepa8b1i8oziumte45jxcgq44lto83dss1ts: { symbol: 'JTO' },
};

@Injectable({ providedIn: 'root' })
export class BurnService {
  private readonly api = inject(ApiService);
  private readonly wallet = inject(WalletService);
  private readonly solanaRpc = inject(SolanaRpcService);

  async loadTokenAccounts(): Promise<TokenAccountInfo[]> {
    const walletAddress = this.wallet.publicKey();
    if (!walletAddress) throw new Error('Wallet not connected');

    const accounts = await this.solanaRpc.getAllTokenAccounts(walletAddress);

    return accounts.map((acc) => {
      const info = TOKEN_SYMBOLS[acc.mint];
      const shortMint = acc.mint.slice(0, 4) + '…' + acc.mint.slice(-4);
      return {
        ...acc,
        symbol: info?.symbol ?? shortMint,
        logoUri: info?.logoUri,
        selected: false,
      };
    });
  }

  async buildCloseAccounts(mints: string[]): Promise<BuildResponse> {
    return firstValueFrom(
      this.api
        .post<BuildResponse>('/actions/build', {
          type: 'close_accounts',
          params: { mints },
        })
        .pipe(timeout(30_000))
    );
  }

  async buildBurn(mint: string, amount: string, closeMint: boolean): Promise<BuildResponse> {
    return firstValueFrom(
      this.api
        .post<BuildResponse>('/actions/build', {
          type: 'burn',
          params: { mint, amount, closeMint },
        })
        .pipe(timeout(30_000))
    );
  }

  async signAndSubmit(transaction: string): Promise<string> {
    const web3 = await import('@solana/web3.js');
    const connection = createSolanaConnection('confirmed');

    const txBuffer = Uint8Array.from(atob(transaction), (c) => c.charCodeAt(0));
    const tx = web3.Transaction.from(txBuffer);

    // Refresh blockhash so it doesn't expire while user is reviewing/signing
    const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash('confirmed');
    tx.recentBlockhash = blockhash;

    const signedTx = (await this.wallet.signTransaction(tx)) as { serialize(): Uint8Array };
    const raw = signedTx.serialize();

    const signature = await connection.sendRawTransaction(raw, {
      skipPreflight: false,
      preflightCommitment: 'confirmed',
    });

    await connection.confirmTransaction(
      { signature, blockhash, lastValidBlockHeight },
      'confirmed'
    );
    return signature;
  }

  formatSol(lamports: number): string {
    return (lamports / LAMPORTS_PER_SOL).toFixed(6);
  }
}
