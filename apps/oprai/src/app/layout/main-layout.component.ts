import { Component, inject, signal, effect, untracked, HostListener, computed, ViewChild, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterOutlet, RouterLink, RouterLinkActive, Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { LucideAngularModule } from 'lucide-angular';
import { Subscription } from 'rxjs';
import { WalletButtonComponent } from '@shared/components/wallet-button/wallet-button.component';
import { LiquidationAlertBannerComponent } from '@shared/components/liquidation-alert-banner/liquidation-alert-banner.component';
import { RiskWarningDialogComponent } from '@shared/components/risk-warning-dialog/risk-warning-dialog.component';
import { TruncateAddressPipe } from '@shared/pipes/truncate-address.pipe';
import { AuthService } from '@core/services/auth.service';
import { WalletService } from '@core/services/wallet.service';
import { ThemeService, ThemePreference } from '@core/services/theme.service';
import { SessionStorageService, ChatSession, SessionGroup } from '@core/services/session-storage.service';
import { ChatApiService, ChatMessage } from '@features/chat/services/chat-api.service';
import { PositionMonitorService } from '@core/services/position-monitor.service';
import { SpendingLimitService } from '@core/services/spending-limit.service';

export type SettingsTab = 'account' | 'appearance' | 'behavior' | 'data';

// Search result with match info
interface SearchResult {
  session: ChatSession;
  score: number;
  matchType: 'title' | 'fuzzy';
  highlights: { start: number; end: number }[];
}

@Component({
  selector: 'app-main-layout',
  standalone: true,
  imports: [
    CommonModule, RouterOutlet, RouterLink, RouterLinkActive, FormsModule,
    LucideAngularModule, WalletButtonComponent, TruncateAddressPipe,
    LiquidationAlertBannerComponent, RiskWarningDialogComponent,
  ],
  templateUrl: './main-layout.component.html',
  styleUrl: './main-layout.component.scss',
})
export class MainLayoutComponent implements OnDestroy {
  private readonly router = inject(Router);
  readonly authService = inject(AuthService);
  readonly walletService = inject(WalletService);
  readonly themeService = inject(ThemeService);
  readonly sessionStorage = inject(SessionStorageService);
  private readonly chatApi = inject(ChatApiService);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly positionMonitor = inject(PositionMonitorService);
  readonly spendingLimitService = inject(SpendingLimitService);

  @ViewChild('walletBtn') walletBtn!: WalletButtonComponent;

  // Debounced search
  private searchDebounceTimer: ReturnType<typeof setTimeout> | null = null;
  readonly debouncedSearchQuery = signal('');

  readonly sidebarOpen = signal(true);
  readonly sessionsLoading = signal(false);
  readonly menuSessionId = signal<string | null>(null);
  readonly editingSessionId = signal<string | null>(null);
  editingTitle = '';
  readonly deleteConfirmId = signal<string | null>(null);
  readonly walletMenuOpen = signal(false);
  readonly chatMenuOpen = signal(false);
  readonly nextCursor = signal<string | null>(null);
  readonly hasMoreSessions = signal(false);
  readonly loadingMore = signal(false);
  profileMenuPos = { bottom: 0, left: 0 };
  menuPosition = { top: 0, left: 0 };

  readonly incognitoMode = this.sessionStorage.incognitoMode;

  // True while wallet is reconnecting OR auth is resolving (brief flash-prevention phase).
  // Used to show the full-page overlay skeleton on very first entry.
  readonly appLoading = computed(() =>
    this.walletService.connecting() ||
    this.authService.authenticating()
  );

  // True while sessions are loading (after auth).
  // Wallet connecting/authenticating is handled inline by the bottom section's own @else if.
  readonly sidebarLoading = computed(() => this.sessionsLoading());

  // Stays true for 380ms after appLoading→false so the CSS fade-out can complete
  // before @if removes the overlay from the DOM.
  readonly skeletonVisible = signal(true);

  // Chat menu project submenu
  readonly chatMenuProjectOpen = signal(false);

  // Share button copied state
  readonly shareCopied = signal(false);
  private shareCopyTimer: ReturnType<typeof setTimeout> | null = null;

  // Projects (empty until project service is implemented)
  readonly projects = signal<{ id: string; name: string; count: number }[]>([]);
  readonly projectsDropdownOpen = signal(false);

  toggleProjectsDropdown(event: MouseEvent): void {
    event.stopPropagation();
    this.projectsDropdownOpen.update(v => !v);
  }

  // Visible sessions (exclude incognito)
  readonly visibleSessions = computed(() =>
    this.sessionStorage.sessions().filter(s => !s.isIncognito)
  );

  // Grouped visible sessions for sidebar display
  readonly sidebarGroupedSessions = computed<SessionGroup[]>(() => {
    const sessions = this.visibleSessions();
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

    const sortDesc = (a: ChatSession, b: ChatSession) =>
      new Date(b.updatedAt || b.createdAt).getTime() -
      new Date(a.updatedAt || a.createdAt).getTime();

    const result: SessionGroup[] = [];

    // Add Pinned group first if there are pinned sessions
    if (pinned.length > 0) {
      result.push({ label: 'Pinned', sessions: pinned.sort(sortDesc) });
    }

    // Add other groups, each sorted newest-first
    result.push(
      ...Object.entries(groups)
        .filter(([, s]) => s.length > 0)
        .map(([label, s]) => ({ label, sessions: s.sort(sortDesc) }))
    );

    return result;
  });

  // Search popup
  readonly searchOpen = signal(false);
  readonly searchExpanded = signal(false);
  readonly searchPreviewSessionId = signal<string | null>(null);
  readonly searchPreviewLoading = signal(false);
  readonly searchPreviewMessages = signal<ChatMessage[]>([]);
  readonly searchPreviewError = signal<string | null>(null);
  readonly searchEditingSessionId = signal<string | null>(null);
  readonly searchDeleteConfirmId = signal<string | null>(null);
  searchEditingTitle = '';
  searchQuery = '';
  private previewMessagesSub: Subscription | null = null;

  // Professional search results with scoring
  readonly searchResults = computed<SearchResult[]>(() => {
    const q = this.debouncedSearchQuery();
    const sessions = this.visibleSessions();

    if (!q.trim()) {
      return sessions.map(s => ({
        session: s,
        score: 0,
        matchType: 'title' as const,
        highlights: [],
      }));
    }

    const query = q.toLowerCase().trim();
    const queryWords = query.split(/\s+/).filter(w => w.length > 0);
    const results: SearchResult[] = [];

    for (const session of sessions) {
      if (session.isIncognito) continue;

      const titleLower = session.title.toLowerCase();
      let score = 0;
      let matchType: 'title' | 'fuzzy' = 'fuzzy';
      const highlights: { start: number; end: number }[] = [];

      // Exact match (highest priority)
      if (titleLower === query) {
        score = 100;
        matchType = 'title';
        highlights.push({ start: 0, end: session.title.length });
      }
      // Starts with query
      else if (titleLower.startsWith(query)) {
        score = 90;
        matchType = 'title';
        highlights.push({ start: 0, end: query.length });
      }
      // Contains exact query
      else if (titleLower.includes(query)) {
        score = 80;
        matchType = 'title';
        const idx = titleLower.indexOf(query);
        highlights.push({ start: idx, end: idx + query.length });
      }
      // Word-based matching
      else {
        let wordMatches = 0;
        let lastMatchIdx = -1;
        let consecutiveBonus = 0;

        for (const word of queryWords) {
          const wordIdx = titleLower.indexOf(word);
          if (wordIdx !== -1) {
            wordMatches++;
            // Bonus for consecutive words
            if (wordIdx > lastMatchIdx && lastMatchIdx !== -1) {
              consecutiveBonus += 5;
            }
            lastMatchIdx = wordIdx;
            highlights.push({ start: wordIdx, end: wordIdx + word.length });
          }
        }

        if (wordMatches > 0) {
          score = 30 + (wordMatches * 15) + consecutiveBonus;
          matchType = 'fuzzy';
        }
      }

      // Fuzzy character matching for partial inputs
      if (score === 0 && query.length >= 2) {
        const fuzzyScore = this.fuzzyMatch(query, titleLower);
        if (fuzzyScore > 0) {
          score = fuzzyScore;
          matchType = 'fuzzy';
        }
      }

      if (score > 0) {
        results.push({ session, score, matchType, highlights });
      }
    }

    // Sort by score descending
    return results.sort((a, b) => b.score - a.score);
  });

  // Fuzzy match algorithm (similar to VSCode's fuzzy search)
  private fuzzyMatch(query: string, text: string): number {
    let score = 0;
    let queryIdx = 0;
    let consecutiveMatches = 0;

    for (let i = 0; i < text.length && queryIdx < query.length; i++) {
      if (text[i] === query[queryIdx]) {
        // Bonus for matching at word boundaries
        if (i === 0 || text[i - 1] === ' ' || /[A-Z]/.test(text[i])) {
          score += 3;
        } else {
          score += 1;
        }
        // Bonus for consecutive matches
        consecutiveMatches++;
        if (consecutiveMatches > 1) {
          score += consecutiveMatches;
        }
        queryIdx++;
      } else {
        consecutiveMatches = 0;
      }
    }

    // Only return score if all query characters were matched
    return queryIdx === query.length ? score : 0;
  }

  readonly filteredSessions = computed(() => {
    return this.searchResults().map(r => r.session);
  });

  readonly filteredGroupedSessions = computed<SessionGroup[]>(() => {
    const results = this.searchResults();
    const q = this.debouncedSearchQuery().toLowerCase().trim();

    if (!q) {
      // Return all sessions grouped when no search
      return this.sidebarGroupedSessions();
    }

    // Group filtered results
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today.getTime() - 86400000);
    const last7 = new Date(today.getTime() - 7 * 86400000);
    const last30 = new Date(today.getTime() - 30 * 86400000);

    const groups: Record<string, ChatSession[]> = {
      Today: [],
      Yesterday: [],
      'Last 7 days': [],
      'Last 30 days': [],
      Older: [],
    };

    for (const result of results) {
      const session = result.session;
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

    return Object.entries(groups)
      .filter(([, s]) => s.length > 0)
      .map(([label, s]) => ({ label, sessions: s }));
  });
  readonly searchPreviewSession = computed(() => {
    const id = this.searchPreviewSessionId();
    if (!id) return null;
    return this.visibleSessions().find((s) => s.id === id) ?? null;
  });

  // Settings modal
  readonly settingsOpen = signal(false);
  readonly activeSettingsTab = signal<SettingsTab>('account');

  // Settings — Behavior
  slippage = 1;
  preferredDex = 'jupiter';
  notifyTrades = true;
  notifyPriceAlerts = true;
  notifySecurity = true;

  // Settings — Data
  chatHistoryEnabled = true;

  private sessionsLoaded = false;

  constructor() {
    effect(() => {
      const authenticated = this.authService.isAuthenticated();
      if (authenticated && !this.sessionsLoaded) {
        this.sessionsLoaded = true;
        untracked(() => this.loadSessions());
      }
      if (!authenticated) {
        this.sessionsLoaded = false;
      }
    });

    // Keep skeletonVisible true for 380ms after appLoading resolves
    // so the CSS opacity transition has time to complete before @if removes the overlay.
    effect(() => {
      if (!this.appLoading() && this.skeletonVisible()) {
        setTimeout(() => untracked(() => this.skeletonVisible.set(false)), 380);
      }
    });

    // Start/stop position monitor when wallet connects/disconnects
    effect(() => {
      const connected = this.walletService.connected();
      untracked(() => {
        if (connected) {
          this.positionMonitor.start();
        } else {
          this.positionMonitor.stop();
        }
      });
    });

    if (typeof window !== 'undefined' && window.innerWidth <= 768) {
      this.sidebarOpen.set(false);
    }
  }

  ngOnDestroy(): void {
    this.cancelPreviewRequest();
    if (this.searchDebounceTimer) {
      clearTimeout(this.searchDebounceTimer);
      this.searchDebounceTimer = null;
    }
  }

  toggleSidebar(): void {
    this.sidebarOpen.update(v => !v);
  }

  onNewChat(): void {
    this.sessionStorage.triggerNewChat();
    this.router.navigate(['/']);
    if (typeof window !== 'undefined' && window.innerWidth <= 768) {
      this.sidebarOpen.set(false);
    }
  }

  onSelectSession(session: ChatSession): void {
    this.sessionStorage.setActiveSession(session.id);
    const resolvedId = this.sessionStorage.resolveId(session.id);
    this.router.navigate(['/c', resolvedId]);
    if (typeof window !== 'undefined' && window.innerWidth <= 768) {
      this.sidebarOpen.set(false);
    }
  }

  onNavClick(): void {
    if (typeof window !== 'undefined' && window.innerWidth <= 768) {
      this.sidebarOpen.set(false);
    }
  }

  openWalletModal(): void {
    if (this.walletBtn) {
      this.walletBtn.openModal();
    }
  }

  cancelWalletConnect(): void {
    this.authService.cancelAuth();
    this.authService.logout();
    this.walletService.disconnect().catch(() => {});
    this.walletService.cancelConnection();
  }

  toggleWalletMenu(event: MouseEvent): void {
    event.stopPropagation();
    if (this.walletMenuOpen()) {
      this.walletMenuOpen.set(false);
      return;
    }
    const btn = event.currentTarget as HTMLElement;
    const rect = btn.getBoundingClientRect();
    const vh = window.innerHeight;
    this.profileMenuPos = {
      bottom: vh - rect.top + 8,
      left: rect.left,
    };
    this.walletMenuOpen.set(true);
  }

  async disconnectWallet(): Promise<void> {
    this.walletMenuOpen.set(false);
    await this.walletService.disconnect();
    this.authService.logout();
  }

  toggleIncognito(): void {
    if (this.sessionStorage.activeSessionId()) return;
    this.sessionStorage.toggleIncognito();
  }

  togglePin(sessionId?: string): void {
    const id = sessionId ?? this.sessionStorage.activeSessionId();
    if (!id) return;
    const newPinned = !this.sessionStorage.isPinned(id);
    this.sessionStorage.togglePin(id);
    this.chatMenuOpen.set(false);
    const resolved = this.sessionStorage.resolveId(id);
    if (!id.startsWith('local:')) {
      this.chatApi.pinSession(resolved, newPinned).subscribe();
    }
  }

  isPinned(sessionId: string): boolean {
    return this.sessionStorage.isPinned(sessionId);
  }

  isChatRoute(): boolean {
    const url = this.router.url;
    return url === '/' || url.startsWith('/c/');
  }

  toggleChatMenu(event: MouseEvent): void {
    event.stopPropagation();
    this.chatMenuProjectOpen.set(false);
    this.chatMenuOpen.update(v => !v);
  }

  toggleChatMenuProject(event: MouseEvent): void {
    event.stopPropagation();
    this.chatMenuProjectOpen.update(v => !v);
  }

  moveToProject(projectId: string): void {
    const sessionId = this.sessionStorage.activeSessionId();
    if (!sessionId) return;
    // TODO: Call API to move session to project
    console.log('Move session', sessionId, 'to project', projectId);
    this.chatMenuOpen.set(false);
    this.chatMenuProjectOpen.set(false);
  }

  newProject(): void {
    // TODO: Implement project creation
    this.chatMenuOpen.set(false);
  }

  shareChat(): void {
    const sessionId = this.sessionStorage.activeSessionId();
    if (!sessionId) return;
    const resolved = this.sessionStorage.resolveId(sessionId);
    const url = `${window.location.origin}/c/${resolved}`;
    navigator.clipboard.writeText(url).then(() => {
      this.shareCopied.set(true);
      if (this.shareCopyTimer) clearTimeout(this.shareCopyTimer);
      this.shareCopyTimer = setTimeout(() => this.shareCopied.set(false), 2000);
    });
  }

  deleteActiveChat(): void {
    const sessionId = this.sessionStorage.activeSessionId();
    if (!sessionId) return;
    this.chatMenuOpen.set(false);
    this.confirmDelete(sessionId);
  }

  navigateFromMenu(path: string): void {
    this.walletMenuOpen.set(false);
    this.router.navigate([path]);
    this.onNavClick();
  }

  // ── Settings Modal ──

  openSettings(tab: SettingsTab = 'account'): void {
    this.walletMenuOpen.set(false);
    this.activeSettingsTab.set(tab);
    this.settingsOpen.set(true);
  }

  closeSettings(): void {
    this.settingsOpen.set(false);
  }

  setSettingsTab(tab: SettingsTab): void {
    this.activeSettingsTab.set(tab);
  }

  setThemePreference(pref: ThemePreference): void {
    this.themeService.setPreference(pref);
  }

  onLimitChange(field: 'maxPerTxUsd' | 'maxPerDayUsd', event: Event): void {
    const value = Math.max(0, parseFloat((event.target as HTMLInputElement).value) || 0);
    const current = this.spendingLimitService.getLimits();
    this.spendingLimitService.setLimits({ ...current, [field]: value });
  }

  // ── Search ──

  openSearch(): void {
    this.searchOpen.set(true);
    this.searchExpanded.set(false);
    this.searchQuery = '';
    this.debouncedSearchQuery.set('');
    this.seedSearchPreviewSession();
  }

  closeSearch(): void {
    this.searchOpen.set(false);
    this.searchExpanded.set(false);
    this.searchQuery = '';
    this.debouncedSearchQuery.set('');
    this.searchEditingSessionId.set(null);
    this.searchDeleteConfirmId.set(null);
    this.searchEditingTitle = '';
    this.searchPreviewSessionId.set(null);
    this.searchPreviewMessages.set([]);
    this.searchPreviewError.set(null);
    this.cancelPreviewRequest();
    if (this.searchDebounceTimer) {
      clearTimeout(this.searchDebounceTimer);
      this.searchDebounceTimer = null;
    }
  }

  // Debounced search input handler - call this from template
  onSearchInput(): void {
    if (this.searchDebounceTimer) {
      clearTimeout(this.searchDebounceTimer);
    }
    // Instant feedback for short queries, debounce longer ones
    const delay = this.searchQuery.length < 3 ? 50 : 150;
    this.searchDebounceTimer = setTimeout(() => {
      this.debouncedSearchQuery.set(this.searchQuery);
      this.cdr.markForCheck();
    }, delay);
  }

  selectSearchResult(session: ChatSession): void {
    this.closeSearch();
    this.onSelectSession(session);
  }

  newChatFromSearch(): void {
    this.closeSearch();
    this.onNewChat();
  }

  toggleSearchExpanded(): void {
    this.searchExpanded.update((v) => !v);
  }

  onSearchSessionClick(session: ChatSession): void {
    if (this.searchExpanded()) {
      this.setSearchPreviewSession(session);
      return;
    }
    this.selectSearchResult(session);
  }

  openSessionFromSidebar(event: MouseEvent, session: ChatSession): void {
    event.stopPropagation();
    this.onSelectSession(session);
  }

  renameSessionFromSidebar(event: MouseEvent, sessionId: string): void {
    event.stopPropagation();
    this.startRename(sessionId);
  }

  toggleSessionPin(event: MouseEvent, sessionId: string): void {
    event.stopPropagation();
    const newPinned = !this.sessionStorage.isPinned(sessionId);
    this.sessionStorage.togglePin(sessionId);
    const resolved = this.sessionStorage.resolveId(sessionId);
    if (!sessionId.startsWith('local:')) {
      this.chatApi.pinSession(resolved, newPinned).subscribe();
    }
  }

  deleteSessionFromSidebar(event: MouseEvent, sessionId: string): void {
    event.stopPropagation();
    this.requestDelete(sessionId);
  }

  openSessionFromSearch(event: MouseEvent, session: ChatSession): void {
    event.stopPropagation();
    this.selectSearchResult(session);
  }

  renameSessionFromSearch(event: MouseEvent, sessionId: string): void {
    event.stopPropagation();
    const session = this.visibleSessions().find((s) => s.id === sessionId);
    if (!session) return;
    this.searchDeleteConfirmId.set(null);
    this.searchEditingSessionId.set(sessionId);
    this.searchEditingTitle = session.title;
  }

  deleteSessionFromSearch(event: MouseEvent, sessionId: string): void {
    event.stopPropagation();
    this.searchEditingSessionId.set(null);
    this.searchDeleteConfirmId.set(sessionId);
  }

  confirmSearchRename(sessionId: string): void {
    const trimmed = this.searchEditingTitle.trim();
    if (!trimmed) {
      this.cancelSearchRename();
      return;
    }
    this.sessionStorage.updateTitle(sessionId, trimmed);
    const resolved = this.sessionStorage.resolveId(sessionId);
    if (!sessionId.startsWith('local:')) {
      this.chatApi.updateSessionTitle(resolved, trimmed).subscribe();
    }
    this.searchEditingSessionId.set(null);
    this.searchEditingTitle = '';
  }

  cancelSearchRename(): void {
    this.searchEditingSessionId.set(null);
    this.searchEditingTitle = '';
  }

  confirmSearchDelete(sessionId: string): void {
    this.searchDeleteConfirmId.set(null);
    this.confirmDelete(sessionId);
  }

  cancelSearchDelete(): void {
    this.searchDeleteConfirmId.set(null);
  }

  openPreviewSession(): void {
    const session = this.searchPreviewSession();
    if (!session) return;
    this.selectSearchResult(session);
  }

  // ── Context Menu ──

  @HostListener('document:click')
  onDocumentClick(): void {
    if (this.menuSessionId()) this.closeMenu();
    if (this.chatMenuOpen()) this.chatMenuOpen.set(false);
  }

  @HostListener('document:keydown.meta.shift.j', ['$event'])
  onCmdShiftJ(event: Event): void {
    event.preventDefault();
    if (!this.sessionStorage.activeSessionId()) {
      this.toggleIncognito();
    }
  }

  @HostListener('document:keydown.meta.k', ['$event'])
  onCmdK(event: Event): void {
    event.preventDefault();
    if (this.searchOpen()) {
      this.closeSearch();
    } else {
      this.openSearch();
    }
  }

  @HostListener('document:keydown.escape')
  onEscapeKey(): void {
    if (this.settingsOpen()) {
      this.closeSettings();
    } else if (this.searchOpen()) {
      this.closeSearch();
    }
  }

  openMenu(event: MouseEvent, sessionId: string): void {
    event.stopPropagation();
    const btn = event.currentTarget as HTMLElement;
    const rect = btn.getBoundingClientRect();
    this.menuPosition = { top: rect.bottom + 4, left: rect.right - 148 };
    this.clampMenuPosition();
    this.menuSessionId.set(this.menuSessionId() === sessionId ? null : sessionId);
    this.deleteConfirmId.set(null);
  }

  closeMenu(): void {
    this.menuSessionId.set(null);
    this.deleteConfirmId.set(null);
  }

  startRename(sessionId: string): void {
    const session = this.sessionStorage.sessions().find(s => s.id === sessionId);
    if (!session) return;
    this.editingSessionId.set(sessionId);
    this.editingTitle = session.title;
    this.menuSessionId.set(null);
  }

  confirmRename(sessionId: string): void {
    const trimmed = this.editingTitle.trim();
    if (!trimmed) { this.cancelRename(); return; }
    this.sessionStorage.updateTitle(sessionId, trimmed);
    const resolved = this.sessionStorage.resolveId(sessionId);
    if (!sessionId.startsWith('local:')) {
      this.chatApi.updateSessionTitle(resolved, trimmed).subscribe();
    }
    this.editingSessionId.set(null);
    this.editingTitle = '';
  }

  cancelRename(): void {
    this.editingSessionId.set(null);
    this.editingTitle = '';
  }

  requestDelete(sessionId: string): void {
    this.deleteConfirmId.set(sessionId);
    this.menuSessionId.set(null);
  }

  confirmDelete(sessionId: string): void {
    const resolved = this.sessionStorage.resolveId(sessionId);
    if (!sessionId.startsWith('local:')) {
      this.chatApi.deleteSession(resolved).subscribe();
    }
    this.sessionStorage.removeSession(sessionId);
    this.deleteConfirmId.set(null);
    if (this.sessionStorage.activeSessionId() === null) {
      this.router.navigate(['/']);
    }
  }

  cancelDelete(): void {
    this.deleteConfirmId.set(null);
  }

  onContextMenu(event: MouseEvent, sessionId: string): void {
    event.preventDefault();
    event.stopPropagation();
    this.menuPosition = { top: event.clientY, left: event.clientX };
    this.clampMenuPosition();
    this.menuSessionId.set(sessionId);
    this.deleteConfirmId.set(null);
  }

  private clampMenuPosition(): void {
    const menuW = 148;
    const menuH = 128; // 3 items + divider
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    if (this.menuPosition.left + menuW > vw) this.menuPosition.left = vw - menuW - 8;
    if (this.menuPosition.left < 8) this.menuPosition.left = 8;
    if (this.menuPosition.top + menuH > vh) this.menuPosition.top = this.menuPosition.top - menuH - 8;
    if (this.menuPosition.top < 8) this.menuPosition.top = 8;
  }

  private seedSearchPreviewSession(): void {
    const activeId = this.sessionStorage.activeSessionId();
    const active = activeId
      ? this.visibleSessions().find((s) => s.id === activeId) ?? null
      : null;
    const fallback = this.visibleSessions()[0] ?? null;
    const initial = active ?? fallback;
    if (!initial) {
      this.searchPreviewSessionId.set(null);
      this.searchPreviewMessages.set([]);
      return;
    }
    this.setSearchPreviewSession(initial);
  }

  private setSearchPreviewSession(session: ChatSession): void {
    this.searchPreviewSessionId.set(session.id);
    this.searchPreviewError.set(null);
    this.searchPreviewMessages.set([]);

    if (session.id.startsWith('local:')) {
      return;
    }

    this.cancelPreviewRequest();
    this.searchPreviewLoading.set(true);
    const resolvedId = this.sessionStorage.resolveId(session.id);
    this.previewMessagesSub = this.chatApi.getMessages(resolvedId).subscribe({
      next: (messages) => {
        this.searchPreviewMessages.set(messages);
        this.searchPreviewLoading.set(false);
      },
      error: () => {
        this.searchPreviewLoading.set(false);
        this.searchPreviewError.set('Failed to load messages.');
      },
    });
  }

  private cancelPreviewRequest(): void {
    if (!this.previewMessagesSub) return;
    this.previewMessagesSub.unsubscribe();
    this.previewMessagesSub = null;
    this.searchPreviewLoading.set(false);
  }

  onHistoryScroll(event: Event): void {
    if (this.loadingMore() || !this.hasMoreSessions()) return;
    const el = event.target as HTMLElement;
    const threshold = 100;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < threshold) {
      this.loadMoreSessions();
    }
  }

  private loadMoreSessions(): void {
    const cursor = this.nextCursor();
    if (!cursor || this.loadingMore()) return;
    this.loadingMore.set(true);
    this.chatApi.getSessionsPaginated(cursor, 30).subscribe({
      next: (resp) => {
        const mapped = resp.sessions.map((s) => ({
          id: s.id,
          title: s.title ?? 'Untitled',
          createdAt: s.createdAt,
          updatedAt: s.updatedAt ?? s.createdAt,
          isLocal: false,
        }));
        this.sessionStorage.appendSessions(mapped);
        this.nextCursor.set(resp.nextCursor);
        this.hasMoreSessions.set(resp.hasMore);
        this.loadingMore.set(false);
      },
      error: () => {
        this.loadingMore.set(false);
      },
    });
  }

  private loadSessions(): void {
    this.sessionsLoading.set(true);

    // Create a timeout to prevent infinite loading
    const timeout = setTimeout(() => {
      console.warn('Session load timeout - using cached data');
      this.sessionsLoading.set(false);
    }, 10000);

    console.log('[MainLayout] Loading sessions with limit 30');
    this.chatApi.getSessionsPaginated(undefined, 30).subscribe({
      next: (resp) => {
        clearTimeout(timeout);
        console.log('[MainLayout] Sessions response:', {
          count: resp.sessions.length,
          hasMore: resp.hasMore,
          nextCursor: resp.nextCursor
        });
        this.sessionStorage.loadSessions(
          resp.sessions.map((s) => ({
            id: s.id,
            title: s.title ?? 'Untitled',
            pinned: s.pinned ?? false,
            createdAt: s.createdAt,
            updatedAt: s.updatedAt ?? s.createdAt,
            isLocal: false,
          }))
        );
        this.nextCursor.set(resp.nextCursor);
        this.hasMoreSessions.set(resp.hasMore);
        this.sessionsLoading.set(false);
      },
      error: (err) => {
        clearTimeout(timeout);
        console.error('[MainLayout] Failed to load sessions:', err);
        this.sessionsLoading.set(false);
        // Don't reset sessionsLoaded so we don't retry indefinitely
      },
    });
  }
}
