import {
  Component,
  Input,
  Output,
  EventEmitter,
  ElementRef,
  ViewChild,
  AfterViewChecked,
  OnChanges,
  SimpleChanges,
  inject,
  signal,
  computed,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { ChatMessage } from '../../services/chat-api.service';
import { IntentParserService, ParsedAction, ParsedQuery, ParsedClarify } from '../../services/intent-parser.service';
import { PROTOCOLS } from '../../models/protocol-list';
import { ApiService } from '@core/services/api.service';

// Index protocol id → display label once at module load. The chip render
// path runs in the change-detection hot loop, so a precomputed map beats an
// .find() per chip per render.
const PROTOCOL_LABELS: Record<string, string> = Object.fromEntries(
  PROTOCOLS.map(p => [p.id, p.label]),
);
import { ActionCardComponent } from '../action-card/action-card.component';
import { QueryCardComponent } from '../query-card/query-card.component';
import { ClarifyCardComponent } from '../clarify-card/clarify-card.component';
import { MarkdownPipe } from '@shared/pipes/markdown.pipe';

@Component({
  selector: 'app-message-list',
  standalone: true,
  imports: [CommonModule, ActionCardComponent, QueryCardComponent, ClarifyCardComponent, MarkdownPipe],
  templateUrl: './message-list.component.html',
  styleUrl: './message-list.component.scss',
})
export class MessageListComponent implements AfterViewChecked, OnChanges {
  private readonly intentParser = inject(IntentParserService);
  private readonly api = inject(ApiService);

  @Input() messages: ChatMessage[] = [];
  @Input() streaming = false;
  @Input() currentThinking: string | null = null;

  /**
   * Per-message rating tracker. The user's last vote per message is held in
   * memory so the active button stays highlighted; the row is also persisted
   * on the backend so dashboards can aggregate negatives. -1 = thumbs down,
   * +1 = thumbs up, undefined = no rating yet.
   */
  readonly feedbackRatings = signal<Record<string, 1 | -1>>({});

  /**
   * Submit a thumbs vote on an assistant message. Calls
   * POST /messages/{id}/feedback (gateway → chat-service). Optimistic UI:
   * we update the local state first; on backend failure we keep the visual
   * (a single failed POST is better than a flickering button).
   */
  rateMessage(message: ChatMessage, rating: 1 | -1): void {
    if (!message?.id) return;
    if (message.role !== 'assistant') return;
    const current = this.feedbackRatings()[message.id];
    // Re-clicking the active vote toggles it off — but on the backend we
    // just leave the row; a "0" rating violates our CHECK constraint. So
    // a second click on an active vote is a no-op visually too.
    if (current === rating) return;
    this.feedbackRatings.update(m => ({ ...m, [message.id]: rating }));
    this.api.post(`/messages/${message.id}/feedback`, { rating }).subscribe({
      error: () => { /* keep optimistic value; backend will retry on next vote */ },
    });
  }

  // Thinking UI state — keyed to a specific message so it persists after streaming ends
  readonly savedThinkingEntry = signal<{ id: string; text: string } | null>(null);
  readonly thinkingExpanded = signal(false);
  readonly thinkingSummary = computed(() => {
    const t = this.savedThinkingEntry()?.text;
    if (!t) return '';
    const stripped = t.replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\*([^*]+)\*/g, '$1').replace(/#+\s*/g, '').replace(/\n+/g, ' ').trim();
    return stripped.length > 90 ? stripped.slice(0, 90) + '…' : stripped;
  });

  /** Strip markdown markers from streaming thinking for plain-text display. */
  stripThinkingMd(text: string | null): string {
    if (!text) return '';
    return text.replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\*([^*]+)\*/g, '$1').replace(/^#{1,6}\s+/gm, '');
  }

  toggleThinking(): void { this.thinkingExpanded.update(v => !v); }

  ngOnChanges(changes: SimpleChanges): void {
    if ('currentThinking' in changes) {
      const prev: string | null = changes['currentThinking'].previousValue;
      const curr: string | null = changes['currentThinking'].currentValue;
      if (prev && !curr) {
        // Thinking just finished — persist it (shown on last assistant message by position)
        this.savedThinkingEntry.set({ id: '__last__', text: prev });
        this.thinkingExpanded.set(false);
      }
      if (curr && !prev) {
        // New thinking stream started — clear previous
        this.savedThinkingEntry.set(null);
        this.thinkingExpanded.set(false);
      }
    }
    if ('messages' in changes && this.savedThinkingEntry()) {
      // Clear thinking when the user sends a new message (the last message is now from the user)
      const msgs: ChatMessage[] = changes['messages'].currentValue ?? [];
      if (msgs.length > 0 && msgs[msgs.length - 1].role === 'user') {
        this.savedThinkingEntry.set(null);
      }
    }
  }
  @Input() messageActions: Map<string, ParsedAction[]> = new Map();
  @Input() messageQueries: Map<string, ParsedQuery[]> = new Map();
  @Input() messageClarifications: Map<string, ParsedClarify[]> = new Map();
  /**
   * Per-clarify selection state, keyed by `${messageId}:${clarifyIndex}`.
   * Owned by chat-shell so it can be cleared when the user cancels the action
   * the selection spawned, re-enabling the clarify-card buttons.
   */
  @Input() clarifySelections: Map<string, number> = new Map();
  @Output() cancelAction = new EventEmitter<ParsedAction>();
  /**
   * Emitted when a row-level CTA inside a QueryCard (e.g. DLMM "Use this
   * pool") asks the shell to spawn a new action card. Carries the source
   * message id so the shell can link the action to the same conversation
   * thread for context / undo handling.
   */
  @Output() useAction = new EventEmitter<{ sourceMessageId: string; action: ParsedAction }>();
  @Output() requestChat  = new EventEmitter<string>();
  /**
   * Emit { messageId, clarifyIndex, optionIndex, action } so the parent can
   * track the link. `clarifyIndex` is the position of the clarify card within
   * the message (cards can have multiple clarifications); `optionIndex` is
   * which option the user actually picked inside that card — the value the
   * parent must store so the right option stays highlighted.
   */
  @Output() clarifySelected = new EventEmitter<{ messageId: string; clarifyIndex: number; optionIndex: number; action: ParsedAction }>();
  /** Emit when the user cancels an action card (so parent can clear clarify state). */
  @Output() actionDismissed = new EventEmitter<{ messageId: string; action: ParsedAction }>();
  @Output() retryLast = new EventEmitter<void>();
  /** The conversation is full — the user wants a fresh one. */
  @Output() startNewChat = new EventEmitter<void>();
  /** Emitted when the user clicks "Edit message" on a failed assistant turn —
   * parent removes the failed pair and re-populates the composer with the
   * original text so the user can tweak and resend. */
  @Output() editLast = new EventEmitter<void>();
  /**
   * Emitted when the user clicks an inline protocol-mention chip rendered by
   * the markdown pipe (`:orca:` shortcode). Carries the protocol id expected
   * by the message-composer's protocol picker, so the parent can add it to
   * the active selection (same effect as picking from the @ menu).
   */
  @Output() protocolMentionClick = new EventEmitter<string>();
  /**
   * Edit-and-resend an existing user message. Carries the message id so the
   * parent can call the supersede endpoint and prune subsequent messages
   * locally before the new assistant turn streams back.
   */
  @Output() editMessage = new EventEmitter<{ messageId: string; content: string }>();
  /**
   * Retry-from-this-message: resend the EXACT same content as a fresh turn,
   * supersede everything after. Wired to the same backend edit endpoint as
   * an edit, just without changing the text.
   */
  @Output() retryMessage = new EventEmitter<{ messageId: string; content: string }>();

  /** Track which message has the copy tooltip visible */
  readonly copiedMessageId = signal<string | null>(null);

  /** ID of the user message currently being edited inline; null when no row is in edit mode. */
  readonly editingMessageId = signal<string | null>(null);
  /** Working draft inside the edit textarea — committed on save, discarded on cancel. */
  readonly editDraft = signal('');

  startEdit(message: ChatMessage): void {
    if (message.role !== 'user') return;
    // A reply is still streaming — editing/resending now would race the
    // in-flight turn (two responses for one thread). The buttons are also
    // disabled in the template; this guards the programmatic path.
    if (this.streaming) return;
    this.editingMessageId.set(message.id);
    this.editDraft.set(message.content);
  }

  cancelEdit(): void {
    this.editingMessageId.set(null);
    this.editDraft.set('');
  }

  /**
   * Commit the inline edit. We surface ONLY the content + id; the parent owns
   * session/auth/streaming state, so it makes the actual network call.
   */
  saveEdit(message: ChatMessage): void {
    if (this.streaming) return; // a reply is in flight — don't stack a second turn
    const next = this.editDraft().trim();
    if (!next || next === message.content) {
      this.cancelEdit();
      return;
    }
    this.editMessage.emit({ messageId: message.id, content: next });
    this.cancelEdit();
  }

  onEditKeydown(event: KeyboardEvent, message: ChatMessage): void {
    // Cmd/Ctrl+Enter saves; Enter inserts a newline (matches ChatGPT/Claude UX).
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      this.saveEdit(message);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.cancelEdit();
    }
  }

  /** Pretty-printed protocol label for a chip. Falls back to id if unknown. */
  protocolChipLabel(id: string): string {
    return PROTOCOL_LABELS[id] ?? id;
  }

  /** Resend the user message verbatim — same supersede semantics as edit. */
  retryUserMessage(message: ChatMessage): void {
    if (message.role !== 'user') return;
    if (this.streaming) return; // don't resend on top of an in-flight reply
    this.retryMessage.emit({ messageId: message.id, content: message.content });
  }

  /**
   * Compact day/month for the user-bubble toolbar timestamp (matches the
   * "28 Nis" style in the design ref). Falls back to local time on same day.
   */
  formatUserBubbleDate(isoString: string): string {
    const d = new Date(isoString);
    const today = new Date();
    const sameDay = d.getDate() === today.getDate()
      && d.getMonth() === today.getMonth()
      && d.getFullYear() === today.getFullYear();
    if (sameDay) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }

  formatTimestamp(isoString: string): string {
    const d = new Date(isoString);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  copyMessage(message: ChatMessage): void {
    const text = this.getDisplayContent(message);
    navigator.clipboard.writeText(text).then(() => {
      this.copiedMessageId.set(message.id);
      setTimeout(() => this.copiedMessageId.set(null), 2000);
    });
  }

  /**
   * Event-delegation handler on the assistant prose container. The markdown
   * pipe expands `:protocol:` shortcodes into spans carrying both a class
   * (`proto-mention--<id>`) and a `data-protocol-id` attribute. The class is
   * the load-bearing carrier — survives any sanitizer config — while the
   * data attribute is the convenience path. We climb the DOM looking for
   * either, so a click on the inner `<img>` or name still resolves.
   */
  onContentClick(event: Event): void {
    let el = event.target as HTMLElement | null;
    while (el && el.nodeType === 1) {
      const dataId = el.dataset?.['protocolId'];
      if (dataId) {
        event.preventDefault();
        event.stopPropagation();
        this.protocolMentionClick.emit(dataId);
        return;
      }
      const cls = el.getAttribute?.('class') || '';
      const m = cls.match(/(?:^|\s)proto-mention--([a-z0-9_]+)(?:\s|$)/i);
      if (m) {
        event.preventDefault();
        event.stopPropagation();
        this.protocolMentionClick.emit(m[1].toLowerCase());
        return;
      }
      el = el.parentElement;
    }
  }

  // ── Batch execution state ─────────────────────────────────────────────────
  /** Key = messageId, value = index of the currently auto-executing action */
  readonly batchIndex = signal<Map<string, number>>(new Map());
  /** messageIds that are currently in batch-run mode */
  readonly batchRunning = signal<Set<string>>(new Set());

  onCancelAction(action: ParsedAction): void {
    this.cancelAction.emit(action);
  }

  onUseAction(action: ParsedAction, sourceMessageId: string): void {
    this.useAction.emit({ sourceMessageId, action });
  }

  onRequestChat(message: string): void {
    this.requestChat.emit(message);
  }

  /** Start sequential batch execution for all actions in a message. */
  startBatchRun(messageId: string): void {
    const actions = this.getActionsForMessage(messageId);
    if (actions.length < 2) return;
    this.batchRunning.update(s => { const next = new Set(s); next.add(messageId); return next; });
    this.batchIndex.update(m => { const next = new Map(m); next.set(messageId, 0); return next; });
  }

  /** Called by action-card when one action in a batch completes (success or failure). */
  onBatchStepComplete(messageId: string, result: { success: boolean; error?: string }): void {
    if (!this.batchRunning().has(messageId)) return;
    if (!result.success) {
      // Stop batch on failure — LLM feedback already injected by action-card
      this.batchRunning.update(s => { const next = new Set(s); next.delete(messageId); return next; });
      return;
    }
    const actions = this.getActionsForMessage(messageId);
    const currentIdx = this.batchIndex().get(messageId) ?? 0;
    const nextIdx = currentIdx + 1;
    if (nextIdx < actions.length) {
      this.batchIndex.update(m => { const next = new Map(m); next.set(messageId, nextIdx); return next; });
    } else {
      // All done
      this.batchRunning.update(s => { const next = new Set(s); next.delete(messageId); return next; });
    }
  }

  isBatchAction(messageId: string, index: number): boolean {
    return this.batchRunning().has(messageId) && this.batchIndex().get(messageId) === index;
  }

  isBatchRunning(messageId: string): boolean {
    return this.batchRunning().has(messageId);
  }

  @ViewChild('scrollContainer') scrollContainer!: ElementRef<HTMLDivElement>;
  @ViewChild('scrollSpacer') scrollSpacer!: ElementRef<HTMLDivElement>;
  @ViewChild('thinkingBody') thinkingBody?: ElementRef<HTMLDivElement>;

  private previousMessageCount = 0;
  /** true = user hasn't manually scrolled away from bottom */
  private followBottom = true;
  /**
   * While true, ALL scroll logic is frozen — prevents onScroll from resetting
   * followBottom and prevents streaming auto-scroll from overriding the
   * user-message scroll.
   */
  private scrollLocked = false;

  ngAfterViewChecked(): void {
    // Detect new messages added
    if (this.messages.length !== this.previousMessageCount) {
      const grew = this.messages.length > this.previousMessageCount;
      this.previousMessageCount = this.messages.length;
      if (grew) {
        this.scrollToUserMessage();
        return;
      }
    }

    // During streaming: follow bottom ONLY if not locked and user hasn't scrolled away
    if (!this.scrollLocked && this.streaming && this.followBottom) {
      this.scrollToBottom();
    }

    // Auto-scroll the thinking body to the bottom as it streams
    if (this.streaming && this.currentThinking && this.thinkingBody) {
      const el = this.thinkingBody.nativeElement;
      el.scrollTop = el.scrollHeight;
    }

    // Shrink spacer as AI content grows (so no unnecessary white space)
    if (!this.scrollLocked) {
      this.trimSpacer();
    }
  }

  onScroll(): void {
    if (this.scrollLocked) return;
    const el = this.scrollContainer?.nativeElement;
    if (!el) return;
    this.followBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  getDisplayContent(message: ChatMessage): string {
    if (message.role === 'user') return message.content;
    let text = this.intentParser.stripActions(message.content);
    // Strip raw JSON blobs the model leaks into text when it also calls tools.
    // Target only objects that contain known action/query keys.
    text = text.replace(/\{[^{}]*(["']action_type["']|["']query_type["'])[^{}]*\}/gs, '');
    text = text.replace(/To\s*=\s*execute_action\b/g, '');
    return text.trim();
  }

  getActionsForMessage(messageId: string): ParsedAction[] {
    return this.messageActions.get(messageId) ?? [];
  }

  getQueriesForMessage(messageId: string): ParsedQuery[] {
    return this.messageQueries.get(messageId) ?? [];
  }

  getClarificationsForMessage(messageId: string): ParsedClarify[] {
    return this.messageClarifications.get(messageId) ?? [];
  }

  querySnapshotKey(query: ParsedQuery): string {
    const sorted = Object.entries(query.params ?? {}).sort((a, b) => a[0].localeCompare(b[0]));
    return `${query.type}:${JSON.stringify(sorted)}`;
  }

  actionResultKey(action: ParsedAction): string {
    const sorted = Object.entries(action.params ?? {}).sort((a, b) => a[0].localeCompare(b[0]));
    return `${action.type}:${JSON.stringify(sorted)}`;
  }

  onClarifySelected(messageId: string, clarifyIndex: number, payload: { index: number; action: ParsedAction }): void {
    this.clarifySelected.emit({
      messageId,
      clarifyIndex,
      optionIndex: payload.index,
      action: payload.action,
    });
  }

  onActionDismissed(messageId: string, action: ParsedAction): void {
    this.actionDismissed.emit({ messageId, action });
  }

  /** Read-only lookup used by template to disable the right clarify-option. */
  clarifySelectionFor(messageId: string, clarifyIndex: number): number | null {
    const v = this.clarifySelections.get(`${messageId}:${clarifyIndex}`);
    return v === undefined ? null : v;
  }

  isLastAssistantMessage(message: ChatMessage): boolean {
    return this.streaming &&
      message === this.messages[this.messages.length - 1] &&
      message.role === 'assistant';
  }

  isLastAssistantMessageForThinking(message: ChatMessage): boolean {
    for (let i = this.messages.length - 1; i >= 0; i--) {
      if (this.messages[i].role === 'assistant') return this.messages[i] === message;
      if (this.messages[i].role === 'user') return false;
    }
    return false;
  }

  /**
   * Lock all scroll logic, expand the spacer to allow scroll, then
   * position the last user message at the very top of the scroll container.
   */
  private scrollToUserMessage(): void {
    if (this.scrollLocked) return;

    this.scrollLocked = true;
    this.followBottom = false;

    const el = this.scrollContainer?.nativeElement;
    const spacer = this.scrollSpacer?.nativeElement;
    if (!el) {
      this.scrollLocked = false;
      return;
    }

    // Expand spacer to full viewport height so there's enough scrollable room
    if (spacer) spacer.style.minHeight = `${el.clientHeight}px`;

    // 50ms ensures Angular has committed DOM changes + spacer resize
    setTimeout(() => {
      requestAnimationFrame(() => {
        const rows = el.querySelectorAll('.message-row--user');
        const lastUserRow = rows[rows.length - 1] as HTMLElement;
        if (lastUserRow) {
          let offset = 0;
          let node: HTMLElement | null = lastUserRow;
          while (node && node !== el) {
            offset += node.offsetTop;
            node = node.offsetParent as HTMLElement | null;
          }
          el.scrollTop = offset;
        }

        // Immediately trim spacer to minimum needed
        this.trimSpacer();

        setTimeout(() => { this.scrollLocked = false; }, 200);
      });
    }, 50);
  }

  /**
   * Shrink the spacer to the minimum height needed to allow the last user
   * message to be scrollable to the top. Based on user-message offset
   * (not scrollTop) so scrolling up doesn't shrink the spacer.
   */
  private trimSpacer(): void {
    const el = this.scrollContainer?.nativeElement;
    const spacer = this.scrollSpacer?.nativeElement;
    if (!el || !spacer) return;

    const currentSpacerH = spacer.offsetHeight;
    const contentH = el.scrollHeight - currentSpacerH;

    // Find last user message offset
    const rows = el.querySelectorAll('.message-row--user');
    if (rows.length === 0) {
      spacer.style.minHeight = '0px';
      return;
    }
    const lastUserRow = rows[rows.length - 1] as HTMLElement;
    let userOffset = 0;
    let node: HTMLElement | null = lastUserRow;
    while (node && node !== el) {
      userOffset += node.offsetTop;
      node = node.offsetParent as HTMLElement | null;
    }

    // Spacer must be big enough that: contentH + spacer >= userOffset + clientHeight
    const needed = Math.max(0, userOffset + el.clientHeight - contentH);
    if (Math.abs(currentSpacerH - needed) > 2) {
      spacer.style.minHeight = `${needed}px`;
    }
  }

  private scrollToBottom(): void {
    const el = this.scrollContainer?.nativeElement;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }
}
