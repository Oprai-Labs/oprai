import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from './api.service';

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

export interface MemoryPoint {
  id: string;
  payload: {
    summary?: string;
    type?: string;
    conversation_id?: string;
    wallet?: string;
    timestamp?: string;
    [key: string]: unknown;
  };
  score?: number;
}

export interface ConsentFlags {
  position: boolean;
  contract: boolean;
  strategy: boolean;
  preference: boolean;
  decision: boolean;
}

export interface SummarizeRequest {
  conversation_id: string;
  chunk: string;
  token_count?: number;
}

export interface SummarizeResponse {
  summary: string;
  stored: boolean;
  memory_id?: string;
}

// ──────────────────────────────────────────────────────────────────────────────
// Service
// ──────────────────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class MemoryService {
  private readonly api = inject(ApiService);

  /** Get all memories for the current user. */
  async getMemories(): Promise<MemoryPoint[]> {
    try {
      const resp = await firstValueFrom(
        this.api.get<MemoryPoint[]>('/memory')
      );
      return resp ?? [];
    } catch (err) {
      console.error('Failed to fetch memories:', err);
      return [];
    }
  }

  /** Store a new memory point. */
  async storeMemory(payload: MemoryPoint['payload']): Promise<MemoryPoint | null> {
    try {
      const resp = await firstValueFrom(
        this.api.post<MemoryPoint>('/memory', { payload })
      );
      return resp;
    } catch (err) {
      console.error('Failed to store memory:', err);
      return null;
    }
  }

  /** Semantic search across memories. */
  async searchMemories(
    query: string,
    options?: { types?: string[]; topK?: number; threshold?: number }
  ): Promise<MemoryPoint[]> {
    try {
      const params: Record<string, string> = { query };
      if (options?.types?.length) params['types'] = options.types.join(',');
      if (options?.topK != null) params['topK'] = String(options.topK);
      if (options?.threshold != null) params['threshold'] = String(options.threshold);

      const resp = await firstValueFrom(
        this.api.get<MemoryPoint[]>('/memory/search', params)
      );
      return resp ?? [];
    } catch (err) {
      console.error('Failed to search memories:', err);
      return [];
    }
  }

  /** Delete a specific memory by ID. */
  async deleteMemory(id: string): Promise<boolean> {
    try {
      await firstValueFrom(
        this.api.delete<void>(`/memory/${id}`)
      );
      return true;
    } catch (err) {
      console.error('Failed to delete memory:', err);
      return false;
    }
  }

  /** Clear all memories for the current user. */
  async clearMemories(): Promise<boolean> {
    try {
      await firstValueFrom(
        this.api.delete<void>('/memory')
      );
      return true;
    } catch (err) {
      console.error('Failed to clear memories:', err);
      return false;
    }
  }

  /** Get consent flags for the current user. */
  async getConsent(): Promise<ConsentFlags | null> {
    try {
      const resp = await firstValueFrom(
        this.api.get<ConsentFlags>('/consent')
      );
      return resp;
    } catch (err) {
      console.error('Failed to fetch consent:', err);
      return null;
    }
  }

  /** Update consent flags for the current user. */
  async updateConsent(flags: Partial<ConsentFlags>): Promise<ConsentFlags | null> {
    try {
      const resp = await firstValueFrom(
        this.api.put<ConsentFlags>('/consent', flags)
      );
      return resp;
    } catch (err) {
      console.error('Failed to update consent:', err);
      return null;
    }
  }

  /**
   * Summarize a conversation chunk and store it in memory.
   * Called automatically after each AI response to build persistent memory.
   */
  async summarize(req: SummarizeRequest): Promise<SummarizeResponse | null> {
    try {
      const resp = await firstValueFrom(
        this.api.post<SummarizeResponse>('/summarize', req)
      );
      return resp;
    } catch (err) {
      // Non-critical — log but don't disrupt chat
      console.warn('Memory summarize failed (non-critical):', err);
      return null;
    }
  }
}
