import { Component, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { ChatApiService, ChatMessage } from '@features/chat/services/chat-api.service';
import { MessageListComponent } from '@features/chat/components/message-list/message-list.component';
import { TPipe } from '@core/i18n';

/**
 * The public face of a shared conversation.
 *
 * Deliberately NOT a child of MainLayoutComponent. Everything that layout
 * provides — the sidebar with the owner's chat history, the wallet button,
 * the composer — belongs to a signed-in user looking at their own account,
 * and none of it should surround someone else's published chat. This route
 * renders its own header and nothing more.
 *
 * Three properties define the page and are enforced, not assumed:
 *
 * 1. **No wallet is needed.** `getSharedChat` hits the one chat route the
 *    gateway leaves unauthenticated. Nothing on this page reads auth state,
 *    so a visitor who has never connected a wallet sees the full history.
 * 2. **Nothing can be sent.** There is no composer in this template — not a
 *    disabled one, none at all — and the message list runs `readOnly`, so
 *    the per-message edit / retry / feedback controls do not exist either.
 * 3. **Nothing calls the authenticated API.** `offline` on the list keeps
 *    query cards on their stored snapshots and stops action cards from
 *    mounting at all. A shared page that quietly fetched would either 401 or,
 *    worse, answer with the *visitor's* balances under the owner's question.
 */
@Component({
  selector: 'app-shared-chat',
  standalone: true,
  imports: [CommonModule, RouterLink, LucideAngularModule, MessageListComponent, TPipe],
  templateUrl: './shared-chat.component.html',
  styleUrl: './shared-chat.component.scss',
})
export class SharedChatComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly chatApi = inject(ChatApiService);

  readonly loading = signal(true);
  /** Set for any failure. A bad token and a revoked link are the same page:
   *  we must not confirm that a token ever existed. */
  readonly notFound = signal(false);
  readonly title = signal('Shared chat');
  readonly sharedAt = signal('');
  readonly messages = signal<ChatMessage[]>([]);

  readonly sharedAtLabel = computed(() => {
    const iso = this.sharedAt();
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  });

  constructor() {
    const token = this.route.snapshot.paramMap.get('token') ?? '';
    if (!token) {
      this.loading.set(false);
      this.notFound.set(true);
      return;
    }
    this.chatApi.getSharedChat(token).subscribe({
      next: (res) => {
        this.title.set(res.title);
        this.sharedAt.set(res.sharedAt);
        this.messages.set(res.messages);
        this.loading.set(false);
      },
      error: () => {
        this.loading.set(false);
        this.notFound.set(true);
      },
    });
  }
}
