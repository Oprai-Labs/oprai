import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';

export type IdentityType = 'solana_wallet' | 'evm_wallet' | 'telegram' | 'email';

export interface LinkedIdentity {
  id: string;
  type: IdentityType;
  chain?: string;
  identifier: string;
  isPrimary: boolean;
  label?: string;
  verifiedAt: string;
}

export interface AccountMe {
  accountId: string;
  identities: LinkedIdentity[];
  linked?: boolean;
  unlinked?: boolean;
  alreadyLinked?: boolean;
}

/**
 * The multichain account: one OPRAI account (accountId) that owns many linked
 * identities — Solana wallets today, EVM wallets / Telegram tomorrow. Backed by
 * the gateway /account/* endpoints.
 */
@Injectable({ providedIn: 'root' })
export class AccountService {
  private readonly api = inject(ApiService);

  /** The account and every identity linked to it (primary first). */
  getMe(): Observable<AccountMe> {
    return this.api.get<AccountMe>('/account/me');
  }

  /** Detach a linked identity (secondary only — the primary is protected). */
  unlink(id: string): Observable<AccountMe> {
    return this.api.delete<AccountMe>(`/account/identity/${id}`);
  }

  /** Step 1 of linking a new wallet: get a challenge to sign. */
  linkNonce(): Observable<{ nonce: string; nonceId: string }> {
    return this.api.post<{ nonce: string; nonceId: string }>('/account/link/nonce', {});
  }

  /** Step 2: prove control of the wallet by signing the challenge. */
  linkVerify(walletAddress: string, signature: string, nonceId: string): Observable<AccountMe> {
    return this.api.post<AccountMe>('/account/link/verify', { walletAddress, signature, nonceId });
  }
}
