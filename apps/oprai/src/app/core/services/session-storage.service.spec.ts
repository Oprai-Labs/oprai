import { TestBed } from '@angular/core/testing';
import {
  SessionStorageService,
  ChatSession,
  SessionGroup,
  sessionActivityAt,
} from './session-storage.service';

describe('SessionStorageService', () => {
  let service: SessionStorageService;
  // Wallet-scoped storage requires a bound wallet — use the same fixed test wallet
  // throughout so existing localStorage assertions read the right key.
  const TEST_WALLET = 'test-wallet';
  const SESSIONS_KEY = `oprai-sessions:${TEST_WALLET}`;
  const ALIASES_KEY = `oprai-session-aliases:${TEST_WALLET}`;

  const createSession = (overrides: Partial<ChatSession> = {}): ChatSession => ({
    id: `session-${Date.now()}-${Math.random()}`,
    title: 'Test Session',
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    isLocal: true,
    ...overrides,
  });

  beforeEach(() => {
    localStorage.clear();
    TestBed.configureTestingModule({
      providers: [SessionStorageService],
    });
    service = TestBed.inject(SessionStorageService);
    // Bind a wallet so persistence tests can read/write under a stable key.
    service.setWallet(TEST_WALLET);
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  // =====================
  // INITIAL STATE TESTS
  // =====================

  describe('Initial state', () => {
    it('should have empty sessions initially', () => {
      expect(service.sessions()).toEqual([]);
    });

    it('should have null activeSessionId initially', () => {
      expect(service.activeSessionId()).toBe(null);
    });

    it('should have incognitoMode as false initially', () => {
      expect(service.incognitoMode()).toBe(false);
    });

    it('should have null activeSession computed initially', () => {
      expect(service.activeSession()).toBe(null);
    });

    it('should have empty groupedSessions computed initially', () => {
      expect(service.groupedSessions()).toEqual([]);
    });
  });

  // =====================
  // CREATE LOCAL SESSION TESTS
  // =====================

  describe('createLocalSession()', () => {
    it('should create a new local session with default title', () => {
      const session = service.createLocalSession();

      expect(session).toBeDefined();
      expect(session.id).toContain('local:');
      expect(session.title).toBe('New Chat');
      expect(session.isLocal).toBe(true);
    });

    it('should create a new local session with custom title', () => {
      const session = service.createLocalSession('Custom Title');

      expect(session.title).toBe('Custom Title');
    });

    it('should add session to sessions array', () => {
      service.createLocalSession();
      const sessions = service.sessions();

      expect(sessions.length).toBe(1);
    });

    it('should set newly created session as active', () => {
      const session = service.createLocalSession();

      expect(service.activeSessionId()).toBe(session.id);
    });

    it('should create multiple sessions with unique IDs', () => {
      const session1 = service.createLocalSession('Session 1');
      const session2 = service.createLocalSession('Session 2');

      expect(session1.id).not.toBe(session2.id);
    });

    it('should set isIncognito based on current mode', () => {
      service.toggleIncognito();
      const session = service.createLocalSession();

      expect(session.isIncognito).toBe(true);
    });

    it('should have createdAt and updatedAt timestamps', () => {
      const before = new Date().toISOString();
      const session = service.createLocalSession();
      const after = new Date().toISOString();

      expect(session.createdAt).toBeDefined();
      expect(session.updatedAt).toBeDefined();
      expect(new Date(session.createdAt).getTime()).toBeGreaterThanOrEqual(
        new Date(before).getTime()
      );
    });
  });

  // =====================
  // ACTIVE SESSION TESTS
  // =====================

  describe('setActiveSession()', () => {
    it('should set active session by ID', () => {
      const session = service.createLocalSession('Test');

      service.setActiveSession(session.id);

      expect(service.activeSessionId()).toBe(session.id);
      expect(service.activeSession()).toEqual(session);
    });

    it('should clear active session when null', () => {
      const session = service.createLocalSession('Test');
      service.setActiveSession(null);

      expect(service.activeSessionId()).toBe(null);
      expect(service.activeSession()).toBe(null);
    });

    it('should return null for non-existent session', () => {
      service.setActiveSession('non-existent-id');

      expect(service.activeSessionId()).toBe('non-existent-id');
      expect(service.activeSession()).toBe(null);
    });
  });

  // =====================
  // UPDATE TITLE TESTS
  // =====================

  describe('updateTitle()', () => {
    it('should update session title', () => {
      const session = service.createLocalSession('Old Title');

      service.updateTitle(session.id, 'New Title');

      const updated = service.sessions().find((s) => s.id === session.id);
      expect(updated?.title).toBe('New Title');
    });

    // These two used to assert the opposite — that renaming bumped the
    // timestamp and floated the chat to the top. That was the bug: a chat
    // from last month, relabelled, reported itself under "Today". Renaming
    // changes what a conversation is called, not when it happened.
    it('should not change the activity time', (done) => {
      const session = service.createLocalSession('Title');
      const before = sessionActivityAt(session);

      setTimeout(() => {
        service.updateTitle(session.id, 'New Title');

        const updated = service.sessions().find((s) => s.id === session.id);
        expect(sessionActivityAt(updated!)).toBe(before);
        done();
      }, 10);
    });

    it('should not re-order sessions', () => {
      const session1 = service.createLocalSession('Session 1');
      const session2 = service.createLocalSession('Session 2');

      // session1 is the most recently active one.
      service.touchSession(session1.id);

      // Renaming session2 must not steal the top slot from it.
      service.updateTitle(session2.id, 'Updated');

      expect(service.sessions()[0].id).toBe(session1.id);
    });
  });

  // =====================
  // TOUCH SESSION TESTS
  // =====================

  describe('touchSession()', () => {
    it('should update session timestamp', (done) => {
      const session = service.createLocalSession('Test');
      const originalUpdatedAt = session.updatedAt;

      setTimeout(() => {
        service.touchSession(session.id);

        const touched = service.sessions().find((s) => s.id === session.id);
        expect(
          new Date(touched!.updatedAt!).getTime()
        ).toBeGreaterThan(new Date(originalUpdatedAt!).getTime());
        done();
      }, 10);
    });

    it('should move touched session to top', () => {
      const session1 = service.createLocalSession('Session 1');
      const session2 = service.createLocalSession('Session 2');

      service.touchSession(session1.id);

      const sessions = service.sessions();
      expect(sessions[0].id).toBe(session1.id);
    });
  });

  // =====================
  // TOGGLE PIN TESTS
  // =====================

  describe('togglePin()', () => {
    it('should toggle pin status', () => {
      const session = service.createLocalSession('Test');

      service.togglePin(session.id);

      const updated = service.sessions().find((s) => s.id === session.id);
      expect(updated?.pinned).toBe(true);

      service.togglePin(session.id);

      const updatedAgain = service.sessions().find((s) => s.id === session.id);
      expect(updatedAgain?.pinned).toBe(false);
    });
  });

  describe('isPinned()', () => {
    it('should return true for pinned session', () => {
      const session = service.createLocalSession('Test');
      service.togglePin(session.id);

      expect(service.isPinned(session.id)).toBe(true);
    });

    it('should return false for non-pinned session', () => {
      const session = service.createLocalSession('Test');

      expect(service.isPinned(session.id)).toBe(false);
    });

    it('should return false for non-existent session', () => {
      expect(service.isPinned('non-existent')).toBe(false);
    });
  });

  // =====================
  // REMOVE SESSION TESTS
  // =====================

  describe('removeSession()', () => {
    it('should remove session from list', () => {
      const session = service.createLocalSession('Test');

      service.removeSession(session.id);

      const sessions = service.sessions();
      expect(sessions.length).toBe(0);
    });

    it('should clear active session if removed', () => {
      const session = service.createLocalSession('Test');

      service.removeSession(session.id);

      expect(service.activeSessionId()).toBe(null);
    });

    it('should switch to another session if active one removed', () => {
      const session1 = service.createLocalSession('Session 1');
      const session2 = service.createLocalSession('Session 2');

      service.removeSession(session1.id);

      expect(service.activeSessionId()).toBe(session2.id);
    });
  });

  // =====================
  // CLEAR TESTS
  // =====================

  describe('clear()', () => {
    it('should remove all sessions', () => {
      service.createLocalSession('Session 1');
      service.createLocalSession('Session 2');

      service.clear();

      expect(service.sessions()).toEqual([]);
    });

    it('should reset active session', () => {
      service.createLocalSession('Test');

      service.clear();

      expect(service.activeSessionId()).toBe(null);
    });

    it('should clear localStorage', () => {
      service.createLocalSession('Test');

      service.clear();

      expect(localStorage.getItem(SESSIONS_KEY)).toBeNull();
    });
  });

  // =====================
  // GROUPED SESSIONS TESTS
  // =====================

  describe('groupedSessions', () => {
    it('should group sessions by time period', () => {
      const now = new Date();
      const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      const yesterday = new Date(today.getTime() - 86400000);
      const last7 = new Date(today.getTime() - 7 * 86400000);

      service.createLocalSession('Today');
      const sessionYesterday = createSession({
        title: 'Yesterday',
        createdAt: yesterday.toISOString(),
        updatedAt: yesterday.toISOString(),
      });
      const sessionLast7 = createSession({
        title: 'Last 7',
        createdAt: last7.toISOString(),
        updatedAt: last7.toISOString(),
      });

      service.loadSessions([sessionYesterday, sessionLast7]);

      const groups = service.groupedSessions();
      const labels = groups.map((g: SessionGroup) => g.label);

      expect(labels).toContain('Today');
      expect(labels).toContain('Yesterday');
      expect(labels).toContain('Last 7 days');
    });

    it('should put pinned sessions in Pinned group', () => {
      const session = service.createLocalSession('Pinned Test');
      service.togglePin(session.id);

      const groups = service.groupedSessions();
      const pinnedGroup = groups.find((g: SessionGroup) => g.label === 'Pinned');

      expect(pinnedGroup).toBeDefined();
      expect(pinnedGroup!.sessions.length).toBe(1);
    });

    it('should sort sessions by updatedAt descending', () => {
      const now = new Date();
      const older = new Date(now.getTime() - 3600000).toISOString();
      const newer = now.toISOString();

      const sessionOlder = createSession({
        title: 'Older',
        createdAt: older,
        updatedAt: older,
      });
      const sessionNewer = createSession({
        title: 'Newer',
        createdAt: newer,
        updatedAt: newer,
      });

      service.loadSessions([sessionOlder, sessionNewer]);

      const sessions = service.sessions();
      expect(sessions[0].title).toBe('Newer');
      expect(sessions[1].title).toBe('Older');
    });

    it('should return empty array when no sessions', () => {
      expect(service.groupedSessions()).toEqual([]);
    });

    // The reported bug, end to end: rename a month-old chat and it appeared
    // under "Today". Grouping followed updatedAt, which every write bumps.
    it('should keep a renamed old session in its original group', () => {
      const longAgo = new Date(Date.now() - 20 * 86400000).toISOString();
      const old = createSession({
        title: 'From weeks ago',
        createdAt: longAgo,
        updatedAt: longAgo,
        lastMessageAt: longAgo,
      });
      service.loadSessions([old]);

      const groupOf = (title: string) =>
        service.groupedSessions()
          .find((g: SessionGroup) => g.sessions.some((s) => s.title === title))
          ?.label;

      expect(groupOf('From weeks ago')).toBe('Last 30 days');

      service.updateTitle(old.id, 'Renamed today');

      expect(groupOf('Renamed today')).toBe('Last 30 days');
    });

    // A server row whose title was edited has a fresh updatedAt but an old
    // last_message_at. The group must follow the message, not the edit.
    it('should group on lastMessageAt, not updatedAt', () => {
      const longAgo = new Date(Date.now() - 20 * 86400000).toISOString();
      service.loadSessions([
        createSession({
          title: 'Edited server-side',
          createdAt: longAgo,
          updatedAt: new Date().toISOString(),
          lastMessageAt: longAgo,
        }),
      ]);

      const label = service.groupedSessions()
        .find((g: SessionGroup) => g.sessions.some((s) => s.title === 'Edited server-side'))
        ?.label;

      expect(label).toBe('Last 30 days');
    });
  });

  // =====================
  // LOAD SESSIONS TESTS
  // =====================

  describe('loadSessions()', () => {
    it('should load server sessions', () => {
      const serverSessions: ChatSession[] = [
        createSession({ id: 'server-1', isLocal: false }),
        createSession({ id: 'server-2', isLocal: false }),
      ];

      service.loadSessions(serverSessions);

      expect(service.sessions().length).toBe(2);
    });

    it('should merge with existing local sessions', () => {
      const local = service.createLocalSession('Local');
      const server: ChatSession = createSession({
        id: 'server-1',
        isLocal: false,
      });

      service.loadSessions([server]);

      const sessions = service.sessions();
      expect(sessions.length).toBe(2);
      expect(sessions.find((s) => s.id === local.id)).toBeDefined();
      expect(sessions.find((s) => s.id === 'server-1')).toBeDefined();
    });

    it('should preserve local updatedAt if more recent', () => {
      const local = createSession({
        id: 'shared-id',
        createdAt: new Date(Date.now() - 1000).toISOString(),
        updatedAt: new Date(Date.now() - 1000).toISOString(),
      });
      const server = createSession({
        id: 'shared-id',
        createdAt: new Date(Date.now() - 5000).toISOString(),
        updatedAt: new Date(Date.now() - 5000).toISOString(),
      });

      service.loadSessions([local, server]);

      const merged = service.sessions()[0];
      expect(merged.updatedAt).toBe(local.updatedAt);
    });
  });

  describe('appendSessions()', () => {
    it('should append new sessions without duplicates', () => {
      const session1 = createSession({ id: 'existing' });
      service.loadSessions([session1]);

      const session2 = createSession({ id: 'new' });
      service.appendSessions([session2]);

      expect(service.sessions().length).toBe(2);
    });

    it('should not duplicate existing sessions', () => {
      const session = createSession({ id: 'existing' });
      service.loadSessions([session]);

      service.appendSessions([session]);

      expect(service.sessions().length).toBe(1);
    });
  });

  // =====================
  // INCOGNITO MODE TESTS
  // =====================

  describe('toggleIncognito()', () => {
    it('should toggle incognito mode', () => {
      expect(service.incognitoMode()).toBe(false);

      service.toggleIncognito();

      expect(service.incognitoMode()).toBe(true);

      service.toggleIncognito();

      expect(service.incognitoMode()).toBe(false);
    });
  });

  // =====================
  // SESSION ALIAS TESTS
  // =====================

  describe('aliasSession()', () => {
    it('should map local ID to server ID', () => {
      const localSession = service.createLocalSession('Local');
      const serverId = 'server-123';

      service.aliasSession(localSession.id, serverId);

      expect(service.resolveId(localSession.id)).toBe(serverId);
    });

    it('should update session to non-local', () => {
      const localSession = service.createLocalSession('Local');

      service.aliasSession(localSession.id, 'server-123');

      const updated = service.sessions().find(
        (s) => s.id === localSession.id
      );
      expect(updated?.isLocal).toBe(false);
      expect(updated?.serverId).toBe('server-123');
    });
  });

  describe('resolveId()', () => {
    it('should return original ID if not aliased', () => {
      const originalId = 'original-123';

      expect(service.resolveId(originalId)).toBe(originalId);
    });
  });

  // =====================
  // NEW CHAT TRIGGER TESTS
  // =====================

  describe('triggerNewChat()', () => {
    it('should clear active session', () => {
      service.createLocalSession('Test');
      service.triggerNewChat();

      expect(service.activeSessionId()).toBe(null);
    });

    it('should emit on newChat$ observable', (done) => {
      service.newChat$.subscribe(() => {
        done();
      });

      service.triggerNewChat();
    });
  });

  // =====================
  // LOCALSTORAGE PERSISTENCE TESTS
  // =====================

  describe('LocalStorage persistence', () => {
    it('should persist sessions to localStorage', () => {
      service.createLocalSession('Test');

      const stored = localStorage.getItem(SESSIONS_KEY);
      expect(stored).toBeDefined();

      const parsed = JSON.parse(stored!);
      expect(parsed.length).toBe(1);
    });

    it('should not persist incognito sessions', () => {
      service.toggleIncognito();
      service.createLocalSession('Incognito');

      const stored = localStorage.getItem(SESSIONS_KEY);
      expect(stored).toBeDefined();

      const parsed = JSON.parse(stored!);
      expect(parsed.length).toBe(0);
    });

    it('should load sessions from localStorage on init', () => {
      const storedSessions = [createSession({ title: 'Persisted' })];
      localStorage.setItem(SESSIONS_KEY, JSON.stringify(storedSessions));

      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        providers: [SessionStorageService],
      });
      const newService = TestBed.inject(SessionStorageService);
      // Wallet-scoped storage loads only after binding to a wallet — mirrors
      // production where AuthService.restoreSession() calls setWallet().
      newService.setWallet(TEST_WALLET);

      expect(newService.sessions().length).toBe(1);
      expect(newService.sessions()[0].title).toBe('Persisted');
    });

    it('should persist aliases to localStorage', () => {
      const localSession = service.createLocalSession('Local');
      service.aliasSession(localSession.id, 'server-123');

      const stored = localStorage.getItem(ALIASES_KEY);
      expect(stored).toBeDefined();

      const parsed = JSON.parse(stored!);
      expect(parsed[localSession.id]).toBe('server-123');
    });
  });

  // =====================
  // EDGE CASES
  // =====================

  describe('Edge cases', () => {
    it('should handle corrupted localStorage JSON', () => {
      localStorage.setItem(SESSIONS_KEY, 'invalid-json');

      expect(() => TestBed.inject(SessionStorageService)).not.toThrow();
    });

    it('should handle empty sessions array in localStorage', () => {
      localStorage.setItem(SESSIONS_KEY, '[]');

      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        providers: [SessionStorageService],
      });
      const newService = TestBed.inject(SessionStorageService);
      expect(newService.sessions()).toEqual([]);
    });

    it('should handle missing updatedAt gracefully', () => {
      const session = createSession({ updatedAt: undefined });

      service.loadSessions([session]);

      const loaded = service.sessions()[0];
      expect(loaded.updatedAt).toBeDefined();
    });
  });
});
