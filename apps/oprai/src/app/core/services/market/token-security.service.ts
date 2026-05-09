import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { CacheStore } from '../../utils/cache-store';

export interface SecurityReport {
  score: number;
  risks: SecurityRisk[];
  tokenMeta?: {
    name: string;
    symbol: string;
    mintAuthority: string | null;
    freezeAuthority: string | null;
    supply: number;
    decimals: number;
  };
  topHolders?: { address: string; pct: number }[];
  markets?: { marketType: string; pubkey: string; lp: any }[];
}

export interface SecurityRisk {
  name: string;
  value: string;
  description: string;
  score: number;
  level: 'info' | 'warn' | 'danger' | 'good';
}

@Injectable({ providedIn: 'root' })
export class TokenSecurityService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = environment.apiBase;
  private readonly cache = new CacheStore<SecurityReport>(300_000);

  async getSecurityReport(mint: string): Promise<SecurityReport> {
    const cacheKey = `security:${mint}`;
    const cached = this.cache.get(cacheKey);
    if (cached) return cached;

    try {
      const resp = await firstValueFrom(
        this.http.get<any>(`${this.apiBase}/market/security/${mint}`)
      );

      const report: SecurityReport = {
        score: resp?.score ?? resp?.tokenOverallRisk ?? 0,
        risks: (resp?.risks ?? []).map((r: any) => ({
          name: r.name ?? '',
          value: r.value ?? '',
          description: r.description ?? '',
          score: r.score ?? 0,
          level: this.mapRiskLevel(r.level ?? r.score ?? 0),
        })),
        tokenMeta: resp?.tokenMeta ?? resp?.fileMeta ?? undefined,
        topHolders: resp?.topHolders ?? [],
        markets: resp?.markets ?? [],
      };

      this.cache.set(cacheKey, report);
      return report;
    } catch (err) {
      console.error('Security report error:', err);
      return { score: 0, risks: [] };
    }
  }

  private mapRiskLevel(level: string | number): SecurityRisk['level'] {
    if (typeof level === 'number') {
      if (level >= 80) return 'danger';
      if (level >= 50) return 'warn';
      if (level >= 20) return 'info';
      return 'good';
    }
    const l = String(level).toLowerCase();
    if (l === 'danger' || l === 'critical' || l === 'high') return 'danger';
    if (l === 'warn' || l === 'warning' || l === 'medium') return 'warn';
    if (l === 'good' || l === 'safe' || l === 'low') return 'good';
    return 'info';
  }
}
