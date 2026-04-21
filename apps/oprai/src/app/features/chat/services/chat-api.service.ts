import { Injectable, inject } from '@angular/core';
import { Observable, firstValueFrom, of } from 'rxjs';
import { map, timeout, retry, catchError } from 'rxjs/operators';
import { ApiService } from '@core/services/api.service';

export interface StoredActionResult {
  status: 'submitted' | 'confirmed' | 'error';
  txSignature: string | null;
  errorMessage: string | null;
}

export interface QuerySnapshot {
  type: string;
  data: unknown;
  fetchedAt: string;
}

/** Structured action emitted by the backend via function calling. */
export interface StructuredAction {
  type: string;
  params: Record<string, string>;
  chainFromPrevious?: boolean;
}

/** Structured query emitted by the backend via function calling. */
export interface StructuredQuery {
  type: string;
  params: Record<string, string>;
}

/** Clarification option inside a StructuredClarify. */
export interface StructuredClarifyOption {
  label: string;
  sublabel?: string;
  action: string;
  params: Record<string, string>;
}

/** Structured clarification request emitted by the backend. */
export interface StructuredClarify {
  category: string;
  question: string;
  options: StructuredClarifyOption[];
}

export interface MessageMetadata {
  action_results?: Record<string, StoredActionResult>;
  query_snapshots?: Record<string, QuerySnapshot>;
  /** Validated actions from function calling (stored in DB metadata). */
  actions?: StructuredAction[];
  /** Validated queries from function calling (stored in DB metadata). */
  queries?: StructuredQuery[];
  /** Clarification requests from function calling (stored in DB metadata). */
  clarifications?: StructuredClarify[];
}

export interface ChatMessage {
  id: string;
  sessionId: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  createdAt: string;
  thinking?: string;
  thinkingDurationMs?: number;
  metadata?: MessageMetadata;
  /** True when this message represents a stream/LLM error — shows retry UI. */
  isError?: boolean;
}

/** Raw shape the backend may return — legacy fields bridged to ChatMessage. */
interface RawChatMessage extends Omit<ChatMessage, 'role' | 'createdAt'> {
  role?: 'user' | 'assistant' | 'system';
  createdAt?: string;
  /** Legacy field — backend alias for role. */
  speaker?: 'user' | 'assistant' | 'system';
  /** Legacy field — backend alias for createdAt. */
  timestamp?: string;
}

export interface ChatSessionResponse {
  id: string;
  title: string;
  pinned?: boolean;
  createdAt: string;
  updatedAt?: string;
}

export interface PaginatedSessionsResponse {
  sessions: ChatSessionResponse[];
  hasMore: boolean;
  nextCursor: string | null;
}

export interface Attachment {
  url: string;            // IPFS URL or other URL
  type: 'image' | 'file'; // Attachment type
  filename?: string;      // Original filename
}

@Injectable({ providedIn: 'root' })
export class ChatApiService {
  private readonly api = inject(ApiService);

  /**
   * Fetch all sessions for the authenticated user (legacy, no pagination).
   * Backend returns { sessions: [...] } — unwrap it.
   */
  getSessions(): Observable<ChatSessionResponse[]> {
    return this.api
      .get<{ sessions: ChatSessionResponse[] }>('/chat/sessions')
      .pipe(map((resp) => resp.sessions));
  }

  /**
   * Fetch sessions with cursor-based pagination.
   */
  getSessionsPaginated(cursor?: string, limit = 30): Observable<PaginatedSessionsResponse> {
    const params: Record<string, string> = { limit: String(limit) };
    if (cursor) params['cursor'] = cursor;
    const qs = new URLSearchParams(params).toString();
    return this.api.get<PaginatedSessionsResponse>(`/chat/sessions?${qs}`);
  }

  /**
   * Get a single session by ID.
   * Backend returns { session: {...} } — unwrap it.
   */
  getSession(sessionId: string): Observable<ChatSessionResponse> {
    return this.api
      .get<{ session: ChatSessionResponse }>(`/chat/sessions/${sessionId}`)
      .pipe(map((resp) => resp.session));
  }

  /**
   * Get messages for a session.
   * Backend returns { messages: [...] } with speaker/timestamp fields —
   * unwrap and map to role/createdAt.
   */
  getMessages(sessionId: string): Observable<ChatMessage[]> {
    return this.api
      .get<{ messages: RawChatMessage[] }>(`/chat/sessions/${sessionId}/messages`)
      .pipe(
        map((resp) =>
          resp.messages.map((m): ChatMessage => ({
            id: m.id,
            sessionId,
            role: m.speaker ?? m.role ?? 'user',
            content: m.content,
            createdAt: m.timestamp ?? m.createdAt ?? new Date().toISOString(),
            thinking: m.thinking,
            thinkingDurationMs: m.thinkingDurationMs,
            metadata: m.metadata ?? undefined,
            isError: m.isError,
          }))
        )
      );
  }

  /**
   * Merge patch into a message's metadata (action_results / query_snapshots).
   * Returns a Promise<boolean>: true if the server confirmed the save.
   * Retries up to 2 times with a 1s delay before giving up.
   */
  async updateMessageMeta(
    sessionId: string,
    messageId: string,
    patch: Partial<MessageMetadata>
  ): Promise<boolean> {
    try {
      const result = await firstValueFrom(
        this.api
          .patch<{ ok: boolean }>(
            `/chat/sessions/${sessionId}/messages/${messageId}/metadata`,
            patch
          )
          .pipe(
            timeout(8_000),
            retry({ count: 2, delay: 1_000 }),
            catchError(() => of({ ok: false } as { ok: boolean }))
          )
      );
      return result?.ok === true;
    } catch {
      return false;
    }
  }

  /**
   * Fire-and-forget metadata update — does not block the caller.
   * Use when you want best-effort persistence without awaiting.
   */
  updateMessageMetaFire(
    sessionId: string,
    messageId: string,
    patch: Partial<MessageMetadata>
  ): void {
    this.updateMessageMeta(sessionId, messageId, patch).catch(() => {});
  }

  /**
   * Send a message and receive a streaming response via SSE.
   * Returns an Observable that emits each SSE data chunk.
   */
  sendMessageStream(
    sessionId: string,
    content: string,
    attachments?: Attachment[],
    thinking = false
  ): Observable<string> {
    return this.api.sse('/chat/messages/stream', {
      sessionId,
      content,
      ...(attachments && attachments.length > 0 ? { attachments } : {}),
      ...(thinking ? { thinking: true } : {}),
    });
  }

  /**
   * Send a message without streaming (fallback)
   */
  sendMessage(
    sessionId: string,
    content: string
  ): Observable<{ message: ChatMessage; reply: ChatMessage }> {
    return this.api.post(`/chat/messages`, { sessionId, content });
  }

  /**
   * Delete a chat session
   */
  deleteSession(sessionId: string): Observable<void> {
    return this.api.delete<void>(`/chat/sessions/${sessionId}`);
  }

  /**
   * Pin or unpin a session (persisted on server)
   */
  pinSession(sessionId: string, pinned: boolean): Observable<void> {
    return this.api.patch<void>(`/chat/sessions/${sessionId}/pin`, { pinned });
  }

  /**
   * Update session title
   */
  updateSessionTitle(
    sessionId: string,
    title: string
  ): Observable<ChatSessionResponse> {
    return this.api.patch<ChatSessionResponse>(
      `/chat/sessions/${sessionId}`,
      { title }
    );
  }
}
