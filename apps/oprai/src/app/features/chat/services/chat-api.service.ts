import { Injectable, inject } from '@angular/core';
import { Observable, firstValueFrom, of } from 'rxjs';
import { map, timeout, retry, catchError } from 'rxjs/operators';
import { ApiService } from '@core/services/api.service';

export interface StoredActionResult {
  status: 'submitted' | 'confirmed' | 'error';
  txSignature: string | null;
  errorMessage: string | null;
  /** Params that were actually used when the action was executed (may differ from original LLM params if user edited them). */
  executedParams?: Record<string, string>;
  /** Frozen "You pay / You receive" amounts for a swap, captured at submit so a
   *  re-hydrated card shows exactly what was swapped (not a live/blank quote). */
  /** Frozen at submit. `payUsd`/`recvUsd` are what the two sides were worth at
   *  the moment of the trade — a completed card must not re-price itself from
   *  a live feed, or a memecoin's next tick rewrites what the user paid. */
  swapView?: { pay: string; receive: string; payUsd?: number; recvUsd?: number };
  /** Frozen Jupiter Lend info panel (APY, conversion, deposit) captured at
   *  submit so a completed lend/withdraw card keeps showing WHAT was withdrawn
   *  and from where — rendered statically, without re-running the live loader
   *  (which would flash "Loading protocol data..." for a done transaction). */
  lendSnapshot?: { kind: 'earn' | 'borrow'; data: Record<string, unknown> };
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
  warnUnverifiedDestination?: boolean;
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
  /** Explicit @-mention protocol tags the user picked in the composer. */
  protocols?: string[];
  /** ISO timestamp when this user message replaced an earlier edited turn. */
  edited_at?: string;
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
  /** Set when the turn failed for a reason retrying cannot fix — the
   *  conversation hit its length limit. Offer a new chat, not Retry/Edit. */
  isConversationLimit?: boolean;
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
  // Per-chat usage + lock state. Populated by /sessions/{id} and
  // /sessions list responses so the composer can render the locked
  // state across reloads.
  messageCount?: number;
  totalTokens?: number;
  isLocked?: boolean;
  lockedReason?: string | null;
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
   * Backend returns { messages: [...], session: {isLocked, …} }. We surface
   * both so chat-shell can render the locked composer on reload.
   */
  getMessages(sessionId: string): Observable<{
    messages: ChatMessage[];
    session: {
      isLocked: boolean;
      lockedReason: string | null;
      messageCount: number;
      totalTokens: number;
    };
  }> {
    return this.api
      .get<{
        messages: RawChatMessage[];
        session?: {
          isLocked?: boolean;
          lockedReason?: string | null;
          messageCount?: number;
          totalTokens?: number;
        };
      }>(`/chat/sessions/${sessionId}/messages`)
      .pipe(
        map((resp) => ({
          messages: resp.messages.map((m): ChatMessage => ({
            id: m.id,
            sessionId,
            role: m.speaker ?? m.role ?? 'user',
            content: m.content,
            createdAt: m.timestamp ?? m.createdAt ?? new Date().toISOString(),
            thinking: m.thinking,
            thinkingDurationMs: m.thinkingDurationMs,
            metadata: m.metadata ?? undefined,
            isError: m.isError,
          })),
          session: {
            isLocked:     !!resp.session?.isLocked,
            lockedReason: resp.session?.lockedReason ?? null,
            messageCount: resp.session?.messageCount ?? 0,
            totalTokens:  resp.session?.totalTokens ?? 0,
          },
        })),
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
    thinking = false,
    protocols?: string[]
  ): Observable<string> {
    return this.api.sse('/chat/messages/stream', {
      sessionId,
      content,
      ...(attachments && attachments.length > 0 ? { attachments } : {}),
      ...(thinking ? { thinking: true } : {}),
      ...(protocols && protocols.length > 0 ? { protocols } : {}),
    });
  }

  /**
   * Edit a previous user message and stream the new assistant response.
   *
   * The backend supersedes the target message and every later message
   * (soft-delete via `metadata.superseded_at`), then runs a fresh turn
   * with the new content. The first SSE chunk carries an `edit` event so
   * the client can drop the now-orphaned messages locally.
   */
  editMessageStream(
    sessionId: string,
    messageId: string,
    content: string,
    protocols?: string[]
  ): Observable<string> {
    return this.api.sse('/chat/messages/edit', {
      sessionId,
      messageId,
      content,
      ...(protocols && protocols.length > 0 ? { protocols } : {}),
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
