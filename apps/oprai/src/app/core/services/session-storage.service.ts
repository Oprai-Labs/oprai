import { Injectable, signal, computed } from '@angular/core';
import { Subject } from 'rxjs';

export interface ChatSession {
  id: string;
  title: string;
  createdAt: string;
  /**
   * Row-modification time. Moves when the title changes or the chat is
   * pinned, so it does NOT answer "how recent is this conversation" — use
   * {@link sessionActivityAt} for that.
   */
  updatedAt: string;
  /** When a message last landed here. Server-maintained; absent on a draft. */
  lastMessageAt?: string | null;
  isLocal: boolean;
  serverId?: string;
  isIncognito?: boolean;
  pinned?: boolean;
  projectId?: string;
}

export interface SessionGroup {
  label: string;
  sessions: ChatSession[];
}

/**
 * When this conversation was last *active*, for grouping and ordering.
 *
 * Renaming a chat is not activity. It used to be treated as such — the
 * sidebar grouped on `updatedAt`, which every write bumps — so editing the
 * title of a month-old chat filed it under "Today". The date a conversation
 * belongs to is the date something was said in it.
 *
 * `updatedAt` is deliberately absent from the fallback chain: it is the field
 * that lies. A chat with no messages yet has only ever existed since it was
 * created, so `createdAt` is the honest answer there.
 */
export function sessionActivityAt(session: ChatSession): number {
  const raw = session.lastMessageAt || session.createdAt;
  const t = new Date(raw).getTime();
  return Number.isFinite(t) ? t : 0;
}

/** Newest-activity-first comparator shared by every session list. */
export function byActivityDesc(a: ChatSession, b: ChatSession): number {
  return sessionActivityAt(b) - sessionActivityAt(a);
}

const SESSIONS_KEY_PREFIX = 'oprai-sessions';
const ALIAS_KEY_PREFIX = 'oprai-session-aliases';
// Legacy unscoped keys (pre wallet-scope migration). Read once and cleared.
const LEGACY_SESSIONS_KEY = 'oprai-sessions';
const LEGACY_ALIAS_KEY = 'oprai-session-aliases';

function sessionsKey(wallet: string): string { return `${SESSIONS_KEY_PREFIX}:${wallet}`; }
function aliasKey(wallet: string): string { return `${ALIAS_KEY_PREFIX}:${wallet}`; }

@Injectable({ providedIn: 'root' })
export class SessionStorageService {
  private readonly _sessions = signal<ChatSession[]>([]);
  private readonly _activeSessionId = signal<string | null>(null);
  private readonly _incognitoMode = signal(false);
  private readonly _aliasMap = new Map<string, string>(); // local:{uuid} -> server id
  private readonly _newChatTrigger$ = new Subject<void>();
  readonly newChat$ = this._newChatTrigger$.asObservable();

  readonly sessions = this._sessions.asReadonly();
  readonly activeSessionId = this._activeSessionId.asReadonly();
  readonly incognitoMode = this._incognitoMode.asReadonly();

  // Wallet currently bound to the storage. null = unauthenticated → no persistence,
  // sidebar shows nothing. AuthService calls setWallet() on login/restore/logout.
  private boundWallet: string | null = null;

  /**
   * Bind storage to a wallet (call on login / session restore). Pass null on logout.
   * Switching wallets reloads from the new wallet's namespace; logging out clears
   * the in-memory state so the sidebar is empty until a wallet reconnects.
   */
  setWallet(wallet: string | null): void {
    if (this.boundWallet === wallet) return;
    this.boundWallet = wallet;

    if (wallet === null) {
      this._sessions.set([]);
      this._activeSessionId.set(null);
      this._aliasMap.clear();
      return;
    }

    // First time we see a wallet: migrate any legacy unscoped sessions into this
    // wallet's namespace (one-time best-effort). Then load.
    this.migrateLegacyStorage(wallet);
    this.loadFromStorage();
  }

  toggleIncognito(): void {
    this._incognitoMode.update(v => !v);
  }

  readonly activeSession = computed(() => {
    const id = this._activeSessionId();
    if (!id) return null;
    return this._sessions().find((s) => s.id === id) ?? null;
  });

  readonly groupedSessions = computed<SessionGroup[]>(() => {
    const sessions = this._sessions();
    if (sessions.length === 0) return [];

    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today.getTime() - 86400000);
    const last7 = new Date(today.getTime() - 7 * 86400000);
    const last30 = new Date(today.getTime() - 30 * 86400000);

    // Separate pinned sessions
    const pinned: ChatSession[] = [];
    const unpinned: ChatSession[] = [];

    for (const session of sessions) {
      if (session.pinned) {
        pinned.push(session);
      } else {
        unpinned.push(session);
      }
    }

    const groups: Record<string, ChatSession[]> = {
      Today: [],
      Yesterday: [],
      'Last 7 days': [],
      'Last 30 days': [],
      Older: [],
    };

    for (const session of unpinned) {
      const date = new Date(sessionActivityAt(session));
      if (date >= today) {
        groups['Today'].push(session);
      } else if (date >= yesterday) {
        groups['Yesterday'].push(session);
      } else if (date >= last7) {
        groups['Last 7 days'].push(session);
      } else if (date >= last30) {
        groups['Last 30 days'].push(session);
      } else {
        groups['Older'].push(session);
      }
    }

    // Sort each group by most recent activity (newest first)
    const sortDesc = byActivityDesc;

    const result: SessionGroup[] = [];

    // Add Pinned group first if there are pinned sessions
    if (pinned.length > 0) {
      result.push({ label: 'Pinned', sessions: pinned.sort(sortDesc) });
    }

    // Add other groups
    result.push(
      ...Object.entries(groups)
        .filter(([, s]) => s.length > 0)
        .map(([label, s]) => ({ label, sessions: s.sort(sortDesc) }))
    );

    return result;
  });

  /**
   * Create a new local session (instant, no server call)
   */
  createLocalSession(title: string = 'New Chat'): ChatSession {
    const id = `local:${crypto.randomUUID()}`;
    const now = this.nextTimestamp(this._sessions());
    const session: ChatSession = {
      id,
      title,
      createdAt: now,
      updatedAt: now,
      isLocal: true,
      isIncognito: this._incognitoMode(),
    };

    this._sessions.update((sessions) => [session, ...sessions]);
    this._activeSessionId.set(id);
    this.persist();
    return session;
  }

  /**
   * Alias a local session ID to a server-assigned session ID.
   */
  aliasSession(localId: string, serverId: string): void {
    this._aliasMap.set(localId, serverId);
    this.saveAliases();

    this._sessions.update((sessions) =>
      sessions.map((s) =>
        s.id === localId ? { ...s, serverId, isLocal: false } : s
      )
    );
    this.persist();
  }

  /**
   * Resolve a potentially local ID to its server ID
   */
  resolveId(id: string): string {
    return this._aliasMap.get(id) ?? id;
  }

  /**
   * Set the active session by ID (null clears the active session)
   */
  setActiveSession(id: string | null): void {
    this._activeSessionId.set(id);
  }

  /**
   * Signal a "new chat" intent — clears active session and notifies subscribers.
   * Use this instead of setActiveSession(null) when starting a fresh chat.
   */
  triggerNewChat(): void {
    this._activeSessionId.set(null);
    this._newChatTrigger$.next();
  }

  /**
   * Load sessions from server response — merge with existing local sessions
   * Preserves local updatedAt if it's more recent than server's (for proper sorting)
   */
  loadSessions(serverSessions: ChatSession[]): void {
    const current = this._sessions();
    const currentMap = new Map(current.map(s => [s.id, s]));

    // Keep local-only sessions that haven't been aliased to a server session
    const localOnly = current.filter(s =>
      s.id.startsWith('local:') && s.isLocal && !s.serverId
    );

    // Preserve a local activity time that runs ahead of the server's: a
    // message sent moments ago is real activity the list must reflect before
    // the next /sessions round trip lands. Server pinned state always wins.
    const mergedServerSessions = serverSessions.map(serverSession => {
      const localSession = currentMap.get(serverSession.id);
      if (localSession && sessionActivityAt(localSession) > sessionActivityAt(serverSession)) {
        return {
          ...serverSession,
          lastMessageAt: localSession.lastMessageAt ?? serverSession.lastMessageAt,
          updatedAt: localSession.updatedAt,
        };
      }
      return serverSession;
    });

    // Merge: server sessions + remaining local sessions
    const serverIds = new Set(serverSessions.map(s => s.id));
    const merged = [
      ...localOnly.filter(s => !serverIds.has(s.id)),
      ...mergedServerSessions,
    ].map((s) => ({
      // Backfill updatedAt so callers that pass partial sessions still sort
      // correctly and downstream code can rely on the field being present.
      ...s,
      updatedAt: s.updatedAt || s.createdAt || new Date().toISOString(),
    }));
    // Sort by most recent activity
    merged.sort(byActivityDesc);
    this._sessions.set(merged);
    this.persist();
  }

  /**
   * Append sessions from a subsequent page (dedup by id)
   */
  appendSessions(serverSessions: ChatSession[]): void {
    this._sessions.update((current) => {
      const existingIds = new Set(current.map(s => s.id));
      const newOnes = serverSessions.filter(s => !existingIds.has(s.id));
      return [...current, ...newOnes];
    });
    this.persist();
  }

  /**
   * Rename a session, and leave it exactly where it sits in the list.
   *
   * This used to bump `updatedAt` and re-sort, which jumped a renamed chat to
   * the top under "Today" — a chat from last month, relabelled, reported
   * itself as today's conversation. Renaming changes what a conversation is
   * called, not when it happened.
   */
  updateTitle(id: string, title: string): void {
    this._sessions.update((sessions) =>
      sessions.map((s) => (s.id === id ? { ...s, title } : s)),
    );
    this.persist();
  }

  /**
   * Mark a session as active right now and move it to the top.
   *
   * Called when the user sends a message — which IS activity, so it sets
   * `lastMessageAt` optimistically rather than waiting for the server trigger
   * to be reflected on the next /sessions load.
   */
  touchSession(id: string): void {
    this._sessions.update((sessions) => {
      const now = this.nextTimestamp(sessions);
      const updated = sessions.map((s) =>
        s.id === id ? { ...s, lastMessageAt: now, updatedAt: now } : s
      );
      return updated.sort(byActivityDesc);
    });
    this.persist();
  }

  /**
   * Toggle pin status for a session
   */
  togglePin(id: string): void {
    this._sessions.update((sessions) => {
      return sessions.map((s) =>
        s.id === id ? { ...s, pinned: !s.pinned } : s
      );
    });
    this.persist();
  }

  /**
   * Check if a session is pinned
   */
  isPinned(id: string): boolean {
    return this._sessions().find(s => s.id === id)?.pinned ?? false;
  }

  /**
   * Remove a session
   */
  removeSession(id: string): void {
    this._sessions.update((sessions) => sessions.filter((s) => s.id !== id));

    if (this._activeSessionId() === id) {
      const remaining = this._sessions();
      this._activeSessionId.set(remaining.length > 0 ? remaining[0].id : null);
    }
    this.persist();
  }

  /**
   * Clear all sessions (on logout)
   */
  clear(): void {
    this._sessions.set([]);
    this._activeSessionId.set(null);
    this._aliasMap.clear();
    if (!this.boundWallet) return;
    try {
      localStorage.removeItem(sessionsKey(this.boundWallet));
      localStorage.removeItem(aliasKey(this.boundWallet));
    } catch (err) {
      console.warn('[SessionStorageService] Failed to clear localStorage', err);
    }
  }

  // ── LocalStorage Persistence ──

  /**
   * Produce a timestamp strictly greater than every existing session's updatedAt.
   * Date#toISOString has only millisecond precision; without this, multiple
   * mutations within the same ms tick produce equal timestamps and stable-sort
   * leaves the touched session in its previous slot (sidebar order regresses).
   */
  private nextTimestamp(sessions: ChatSession[]): string {
    let next = Date.now();
    for (const s of sessions) {
      const t = sessionActivityAt(s);
      if (t >= next) next = t + 1;
    }
    return new Date(next).toISOString();
  }

  private persist(): void {
    this.saveToStorage(this._sessions());
  }

  private loadFromStorage(): void {
    if (!this.boundWallet) return;
    try {
      const raw = localStorage.getItem(sessionsKey(this.boundWallet));
      const sessions: ChatSession[] = raw ? JSON.parse(raw) : [];
      this._sessions.set(Array.isArray(sessions) ? sessions : []);
      this._aliasMap.clear();
      const aliasRaw = localStorage.getItem(aliasKey(this.boundWallet));
      if (aliasRaw) {
        const aliases: Record<string, string> = JSON.parse(aliasRaw);
        for (const [k, v] of Object.entries(aliases)) {
          this._aliasMap.set(k, v);
        }
      }
    } catch (err) {
      console.warn('[SessionStorageService] Failed to load sessions from localStorage', err);
    }
  }

  private saveToStorage(sessions: ChatSession[]): void {
    // No-op when unauthenticated — prevents writing to a base/legacy key.
    if (!this.boundWallet) return;
    try {
      // Don't persist incognito sessions
      const toSave = sessions.filter(s => !s.isIncognito);
      localStorage.setItem(sessionsKey(this.boundWallet), JSON.stringify(toSave));
    } catch (err) {
      console.warn('[SessionStorageService] Failed to save sessions to localStorage', err);
    }
  }

  private saveAliases(): void {
    if (!this.boundWallet) return;
    try {
      const obj: Record<string, string> = {};
      for (const [k, v] of this._aliasMap.entries()) {
        obj[k] = v;
      }
      localStorage.setItem(aliasKey(this.boundWallet), JSON.stringify(obj));
    } catch (err) {
      console.warn('[SessionStorageService] Failed to save session aliases to localStorage', err);
    }
  }

  /**
   * One-time migration: if legacy unscoped storage exists AND the new wallet-scoped
   * key is empty, move the legacy data into this wallet's namespace. Always remove
   * the legacy keys afterwards so the next wallet doesn't inherit them.
   */
  private migrateLegacyStorage(wallet: string): void {
    try {
      const legacySessions = localStorage.getItem(LEGACY_SESSIONS_KEY);
      const legacyAliases = localStorage.getItem(LEGACY_ALIAS_KEY);
      if (!legacySessions && !legacyAliases) return;
      const newSessionsKey = sessionsKey(wallet);
      const newAliasKey = aliasKey(wallet);
      // Only fill the wallet-scoped slot if it's empty — never overwrite real data.
      if (legacySessions && !localStorage.getItem(newSessionsKey)) {
        localStorage.setItem(newSessionsKey, legacySessions);
      }
      if (legacyAliases && !localStorage.getItem(newAliasKey)) {
        localStorage.setItem(newAliasKey, legacyAliases);
      }
      localStorage.removeItem(LEGACY_SESSIONS_KEY);
      localStorage.removeItem(LEGACY_ALIAS_KEY);
    } catch (err) {
      console.warn('[SessionStorageService] Legacy migration failed', err);
    }
  }
}
