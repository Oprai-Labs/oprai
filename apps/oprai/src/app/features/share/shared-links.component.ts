import { Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { ChatApiService, ChatShare } from '@features/chat/services/chat-api.service';

/**
 * Everything this wallet has published, and the one place to un-publish it.
 *
 * A share is invisible by design — no badge on the conversation, nothing in
 * the sidebar — so without this page a link, once created, is effectively
 * unrevocable: the user would have to remember which chats they shared. That
 * makes this page part of the sharing feature, not an accessory to it.
 */
@Component({
  selector: 'app-shared-links',
  standalone: true,
  imports: [CommonModule, RouterLink, LucideAngularModule],
  templateUrl: './shared-links.component.html',
  styleUrl: './shared-links.component.scss',
})
export class SharedLinksComponent {
  private readonly chatApi = inject(ChatApiService);

  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly shares = signal<ChatShare[]>([]);
  /** Token of the row whose URL was just copied — drives the transient tick. */
  readonly copiedToken = signal<string | null>(null);
  /** Session id currently being revoked, so only that row's button spins. */
  readonly revoking = signal<string | null>(null);

  private copyTimer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    this.load();
  }

  private load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.chatApi.listShares().subscribe({
      next: (shares) => {
        this.shares.set(shares);
        this.loading.set(false);
      },
      error: () => {
        this.loading.set(false);
        this.error.set('Could not load your shared links.');
      },
    });
  }

  shareUrl(share: ChatShare): string {
    return `${window.location.origin}/share/${share.token}`;
  }

  copy(share: ChatShare): void {
    navigator.clipboard.writeText(this.shareUrl(share)).then(() => {
      this.copiedToken.set(share.token);
      if (this.copyTimer) clearTimeout(this.copyTimer);
      this.copyTimer = setTimeout(() => this.copiedToken.set(null), 2000);
    }).catch(() => {
      this.error.set('Copy blocked by the browser — open the link and copy it from the address bar.');
    });
  }

  revoke(share: ChatShare): void {
    if (this.revoking()) return;
    this.revoking.set(share.sessionId);
    this.error.set(null);
    this.chatApi.revokeShare(share.sessionId).subscribe({
      next: () => {
        // Drop the row locally rather than refetching: the list is small and
        // the server has already agreed the link is gone.
        this.shares.update((rows) => rows.filter((r) => r.token !== share.token));
        this.revoking.set(null);
      },
      error: () => {
        this.revoking.set(null);
        this.error.set('Could not revoke that link. Please try again.');
      },
    });
  }
}
