import { Component, Output, EventEmitter, signal, inject, effect, untracked, OnInit, HostListener } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { AuthService } from '@core/services/auth.service';
import { SessionStorageService, ChatSession } from '@core/services/session-storage.service';
import { ChatApiService } from '../../services/chat-api.service';
import { TPipe } from '@core/i18n';

@Component({
  selector: 'app-sidebar',
  standalone: true,
  imports: [CommonModule, FormsModule, LucideAngularModule, TPipe],
  templateUrl: './sidebar.component.html',
  styleUrl: './sidebar.component.scss',
})
export class SidebarComponent implements OnInit {
  private readonly router = inject(Router);
  private readonly authService = inject(AuthService);
  readonly sessionStorage = inject(SessionStorageService);
  private readonly chatApi = inject(ChatApiService);

  @Output() sessionSelected = new EventEmitter<ChatSession>();

  readonly sessions = this.sessionStorage.sessions;
  readonly groupedSessions = this.sessionStorage.groupedSessions;
  readonly activeSessionId = this.sessionStorage.activeSessionId;

  /**
   * Whether this row is the conversation on screen.
   *
   * A session keeps its `local:` id in the list even after the server assigns
   * a real one — `aliasSession` records the server id alongside rather than
   * replacing it. Navigating to /c/<uuid> then sets the active id to the
   * server form, so a direct `session.id === activeSessionId()` compares two
   * names for the same thread and finds them different. The row stayed
   * unhighlighted and there was no way to tell which chat you were in.
   *
   * Both sides are resolved through the alias map, and the server id is
   * checked too, so the match holds whichever name each side happens to hold.
   */
  isActive(session: { id: string; serverId?: string }): boolean {
    const active = this.activeSessionId();
    if (!active) return false;
    const resolvedActive = this.sessionStorage.resolveId(active);
    return (
      session.id === active ||
      session.serverId === active ||
      this.sessionStorage.resolveId(session.id) === resolvedActive ||
      session.serverId === resolvedActive
    );
  }
  readonly sessionsLoading = signal(false);

  readonly menuSessionId = signal<string | null>(null);
  readonly editingSessionId = signal<string | null>(null);
  editingTitle = '';

  readonly deleteConfirmId = signal<string | null>(null);

  menuPosition = { top: 0, left: 0 };

  private sessionsLoaded = false;

  constructor() {
    effect(() => {
      const authenticated = this.authService.isAuthenticated();
      const authenticating = this.authService.authenticating();
      // Reset the loaded flag whenever auth drops so a subsequent reconnect
      // (possibly to a different wallet) refetches the sidebar from the
      // server. Without this, switching wallets leaves the sidebar empty
      // until a full page reload — even after a successful re-auth.
      if (!authenticated && !authenticating) {
        this.sessionsLoaded = false;
        return;
      }
      // Only load sessions when authenticated AND not currently authenticating
      if (authenticated && !authenticating && !this.sessionsLoaded) {
        this.sessionsLoaded = true;
        untracked(() => this.loadSessions());
      }
    });
  }

  ngOnInit(): void {}

  onNewChat(): void {
    this.menuSessionId.set(null);
    this.sessionStorage.triggerNewChat();
    this.router.navigate(['/']);
  }

  onSelect(session: ChatSession): void {
    if (this.editingSessionId()) return;
    this.sessionStorage.setActiveSession(session.id);
    const resolvedId = this.sessionStorage.resolveId(session.id);
    this.sessionSelected.emit(session);
    this.router.navigate(['/c', resolvedId]);
  }

  @HostListener('document:click')
  onDocumentClick(): void {
    if (this.menuSessionId()) {
      this.closeMenu();
    }
  }

  openMenu(event: MouseEvent, sessionId: string): void {
    event.stopPropagation();
    const btn = event.currentTarget as HTMLElement;
    const rect = btn.getBoundingClientRect();
    this.menuPosition = { top: rect.bottom + 4, left: rect.right - 148 };
    this.clampMenuPosition();
    this.menuSessionId.set(
      this.menuSessionId() === sessionId ? null : sessionId
    );
    this.deleteConfirmId.set(null);
  }

  closeMenu(): void {
    this.menuSessionId.set(null);
    this.deleteConfirmId.set(null);
  }

  startRename(sessionId: string): void {
    const session = this.sessions().find((s) => s.id === sessionId);
    if (!session) return;
    this.editingSessionId.set(sessionId);
    this.editingTitle = session.title;
    this.menuSessionId.set(null);
  }

  confirmRename(sessionId: string): void {
    const trimmed = this.editingTitle.trim();
    if (!trimmed) {
      this.cancelRename();
      return;
    }
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

  togglePin(sessionId: string): void {
    const newPinned = !this.sessionStorage.isPinned(sessionId);
    this.sessionStorage.togglePin(sessionId);
    this.menuSessionId.set(null);
    const resolved = this.sessionStorage.resolveId(sessionId);
    if (!sessionId.startsWith('local:')) {
      this.chatApi.pinSession(resolved, newPinned).subscribe();
    }
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

    if (this.activeSessionId() === null) {
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

    if (this.menuPosition.left + menuW > vw) {
      this.menuPosition.left = vw - menuW - 8;
    }
    if (this.menuPosition.left < 8) {
      this.menuPosition.left = 8;
    }
    if (this.menuPosition.top + menuH > vh) {
      this.menuPosition.top = this.menuPosition.top - menuH - 8;
    }
    if (this.menuPosition.top < 8) {
      this.menuPosition.top = 8;
    }
  }

  private loadSessions(): void {
    this.sessionsLoading.set(true);
    this.chatApi.getSessions().subscribe({
      next: (sessions) => {
        this.sessionStorage.loadSessions(
          sessions.map((s: any) => ({
            id: s.id,
            title: s.title ?? 'Untitled',
            pinned: s.pinned ?? false,
            createdAt: s.createdAt,
            updatedAt: s.updatedAt ?? s.createdAt,
            lastMessageAt: s.lastMessageAt ?? null,
            isLocal: false,
          }))
        );
        this.sessionsLoading.set(false);
      },
      error: () => {
        this.sessionsLoading.set(false);
        this.sessionsLoaded = false;
      },
    });
  }
}
