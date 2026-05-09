import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../api.service';

/**
 * JupSOL conversion-rate fetcher used by the action card to preview the
 * "you receive" amount before the user confirms a SOL → jupSOL stake.
 *
 * The rate comes from the gateway-proxied `/market/jupsol/exchange-rate`
 * route, which probes Jupiter's Quote API for 1 SOL → jupSOL. We use the
 * swap quote rather than the underlying stake-pool redemption rate because
 * jupSOL stakes execute as Jupiter swaps — the preview should match what
 * the same swap router will actually pay out, including the AMM spread.
 *
 * Returns null on upstream failure — callers must hide the preview rather
 * than fall back to a static constant. (See the user rule: "don't make
 * fallback with 1.0. it's not safe.")
 */
@Injectable({ providedIn: 'root' })
export class JupSolService {
  private readonly api = inject(ApiService);

  async getExchangeRate(): Promise<number | null> {
    try {
      const resp = await firstValueFrom(
        this.api.get<{ jupSolPrice?: string | number }>('/market/jupsol/exchange-rate')
      );
      const raw = resp?.jupSolPrice;
      const v = typeof raw === 'string' ? parseFloat(raw) : (typeof raw === 'number' ? raw : NaN);
      return Number.isFinite(v) && v > 0 ? v : null;
    } catch {
      return null;
    }
  }
}
