import {
  Component,
  Input,
  Output,
  EventEmitter,
  ElementRef,
  ViewChild,
  AfterViewChecked,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { ChatMessage } from '../../services/chat-api.service';
import { IntentParserService, ParsedAction, ParsedQuery, ParsedClarify } from '../../services/intent-parser.service';
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
export class MessageListComponent implements AfterViewChecked {
  private readonly intentParser = inject(IntentParserService);

  @Input() messages: ChatMessage[] = [];
  @Input() streaming = false;
  @Input() currentThinking: string | null = null;
  @Input() messageActions: Map<string, ParsedAction[]> = new Map();
  @Input() messageQueries: Map<string, ParsedQuery[]> = new Map();
  @Input() messageClarifications: Map<string, ParsedClarify[]> = new Map();
  @Output() cancelAction = new EventEmitter<ParsedAction>();
  @Output() requestChat  = new EventEmitter<string>();
  @Output() clarifySelected = new EventEmitter<ParsedAction>();
  @Output() retryLast = new EventEmitter<void>();

  /** Track which message has the copy tooltip visible */
  readonly copiedMessageId = signal<string | null>(null);

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

  // ── Batch execution state ─────────────────────────────────────────────────
  /** Key = messageId, value = index of the currently auto-executing action */
  readonly batchIndex = signal<Map<string, number>>(new Map());
  /** messageIds that are currently in batch-run mode */
  readonly batchRunning = signal<Set<string>>(new Set());

  onCancelAction(action: ParsedAction): void {
    this.cancelAction.emit(action);
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
    return this.intentParser.stripActions(message.content);
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

  onClarifySelected(action: ParsedAction): void {
    this.clarifySelected.emit(action);
  }

  isLastAssistantMessage(message: ChatMessage): boolean {
    return this.streaming &&
      message === this.messages[this.messages.length - 1] &&
      message.role === 'assistant';
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
