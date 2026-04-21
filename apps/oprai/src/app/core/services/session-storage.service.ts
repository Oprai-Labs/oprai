import { Injectable, signal, computed, effect } from '@angular/core';
import { Subject } from 'rxjs';

export interface ChatSession {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
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

const STORAGE_KEY = 'oprai-sessions';
const ALIAS_KEY = 'oprai-session-aliases';

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

  constructor() {
    // Load from localStorage on init
    this.loadFromStorage();

    // Persist to localStorage whenever sessions change
    effect(() => {
      const sessions = this._sessions();
      this.saveToStorage(sessions);
    });
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
      const date = new Date(session.updatedAt || session.createdAt);
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

    // Sort each group by updatedAt descending (newest first)
    const sortDesc = (a: ChatSession, b: ChatSession) =>
      new Date(b.updatedAt || b.createdAt).getTime() -
      new Date(a.updatedAt || a.createdAt).getTime();

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
    const now = new Date().toISOString();
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

    // For server sessions, preserve local updatedAt if it's more recent.
    // Always use server pinned state as the source of truth.
    const mergedServerSessions = serverSessions.map(serverSession => {
      const localSession = currentMap.get(serverSession.id);
      if (localSession) {
        const localUpdatedAt = new Date(localSession.updatedAt || localSession.createdAt).getTime();
        const serverUpdatedAt = new Date(serverSession.updatedAt || serverSession.createdAt).getTime();
        // Use local updatedAt if it's more recent (user interacted with this session)
        if (localUpdatedAt > serverUpdatedAt) {
          return { ...serverSession, updatedAt: localSession.updatedAt };
        }
      }
      return serverSession;
    });

    // Merge: server sessions + remaining local sessions
    const serverIds = new Set(serverSessions.map(s => s.id));
    const merged = [
      ...localOnly.filter(s => !serverIds.has(s.id)),
      ...mergedServerSessions,
    ];
    // Sort by updatedAt descending
    merged.sort((a, b) =>
      new Date(b.updatedAt || b.createdAt).getTime() -
      new Date(a.updatedAt || a.createdAt).getTime()
    );
    this._sessions.set(merged);
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
  }

  /**
   * Update a session's title and bump its updatedAt timestamp
   */
  updateTitle(id: string, title: string): void {
    const now = new Date().toISOString();
    this._sessions.update((sessions) => {
      const updated = sessions.map((s) =>
        s.id === id ? { ...s, title, updatedAt: now } : s
      );
      // Re-sort to put updated session at top
      updated.sort((a, b) =>
        new Date(b.updatedAt || b.createdAt).getTime() -
        new Date(a.updatedAt || a.createdAt).getTime()
      );
      return updated;
    });
  }

  /**
   * Touch a session (update its updatedAt) to move it to top of list
   */
  touchSession(id: string): void {
    const now = new Date().toISOString();
    this._sessions.update((sessions) => {
      const updated = sessions.map((s) =>
        s.id === id ? { ...s, updatedAt: now } : s
      );
      // Re-sort to put touched session at top
      updated.sort((a, b) =>
        new Date(b.updatedAt || b.createdAt).getTime() -
        new Date(a.updatedAt || a.createdAt).getTime()
      );
      return updated;
    });
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
  }

  /**
   * Clear all sessions (on logout)
   */
  clear(): void {
    this._sessions.set([]);
    this._activeSessionId.set(null);
    this._aliasMap.clear();
    try {
      localStorage.removeItem(STORAGE_KEY);
      localStorage.removeItem(ALIAS_KEY);
    } catch (err) {
      console.warn('[SessionStorageService] Failed to clear localStorage', err);
    }
  }

  // ── LocalStorage Persistence ──

  private loadFromStorage(): void {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const sessions: ChatSession[] = JSON.parse(raw);
        if (Array.isArray(sessions) && sessions.length > 0) {
          this._sessions.set(sessions);
        }
      }
      const aliasRaw = localStorage.getItem(ALIAS_KEY);
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
    try {
      // Don't persist incognito sessions
      const toSave = sessions.filter(s => !s.isIncognito);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
    } catch (err) {
      console.warn('[SessionStorageService] Failed to save sessions to localStorage', err);
    }
  }

  private saveAliases(): void {
    try {
      const obj: Record<string, string> = {};
      for (const [k, v] of this._aliasMap.entries()) {
        obj[k] = v;
      }
      localStorage.setItem(ALIAS_KEY, JSON.stringify(obj));
    } catch (err) {
      console.warn('[SessionStorageService] Failed to save session aliases to localStorage', err);
    }
  }
}
