import { Component, inject, signal, OnInit, OnDestroy } from '@angular/core';
import { CommonModule, Location } from '@angular/common';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { WalletService } from '@core/services/wallet.service';
import { AuthService } from '@core/services/auth.service';
import { SessionStorageService } from '@core/services/session-storage.service';
import {
  ChatApiService,
  ChatMessage,
  Attachment,
  StructuredAction,
  StructuredQuery,
  StructuredClarify,
} from '../../services/chat-api.service';
import { IntentParserService, ParsedAction, ParsedQuery, ParsedClarify } from '../../services/intent-parser.service';
import { SolanaActionService, ActionCallbacks } from '../../services/solana-action.service';
import { MessageListComponent } from '../../components/message-list/message-list.component';
import { MessageComposerComponent } from '../../components/message-composer/message-composer.component';
import { UploadResult } from '@core/services/upload.service';
import { MemoryService } from '@core/services/memory.service';
import { LiquidationMonitorService } from '@core/services/liquidation-monitor.service';

@Component({
  selector: 'app-chat-shell',
  standalone: true,
  imports: [
    CommonModule,
    MessageListComponent,
    MessageComposerComponent,
  ],
  templateUrl: './chat-shell.component.html',
  styleUrl: './chat-shell.component.scss',
})
export class ChatShellComponent implements OnInit, OnDestroy {
  readonly walletService = inject(WalletService);
  readonly authService = inject(AuthService);
  readonly sessionStorage = inject(SessionStorageService);
  private readonly chatApi = inject(ChatApiService);
  private readonly intentParser = inject(IntentParserService);
  private readonly memoryService = inject(MemoryService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly location = inject(Location);

  readonly messages = signal<ChatMessage[]>([]);
  readonly streaming = signal(false);
  readonly loadingMessages = signal(false);
  readonly authError = signal<string | null>(null);
  readonly currentThinking = signal<string | null>(null);
  readonly chatLimitReached = signal(false);
  readonly messageActions = signal<Map<string, ParsedAction[]>>(new Map());
  readonly messageQueries = signal<Map<string, ParsedQuery[]>>(new Map());
  readonly messageClarifications = signal<Map<string, ParsedClarify[]>>(new Map());
  private readonly solanaActionService = inject(SolanaActionService);
  private readonly liquidationMonitor = inject(LiquidationMonitorService);
  readonly currentAttachments = signal<Attachment[]>([]);
  readonly initialLoading = signal(true);
  /** Content of the last failed message — shown with a Retry button. */
  readonly lastFailedContent = signal<string | null>(null);
  private streamSub?: Subscription;
  private routeSub?: Subscription;
  private newChatSub?: Subscription;
  private liquidationSub?: Subscription;
  /** Track already-alerted protocol+market combos to avoid spam (reset on wallet change) */
  private _alertedKeys = new Set<string>();

  // ── Structured intent buffer (from function calling SSE events) ──
  // Accumulated during streaming, applied in finishStream().
  // Takes priority over text-based parsing for new messages.
  private _pendingActions: StructuredAction[] = [];
  private _pendingQueries: StructuredQuery[] = [];
  private _pendingClarifications: StructuredClarify[] = [];

  // ── Typewriter buffer ──
  // Fast typewriter effect: reveals text quickly for snappy feel
  private _revealBuffer = '';
  private _thinkingBuffer = '';
  private _revealTimer: ReturnType<typeof setInterval> | null = null;
  private _streamDone = false;
  private readonly REVEAL_INTERVAL_MS = 8;
  private readonly CHARS_PER_TICK = 6;

  ngOnInit(): void {
    console.log('[ChatShell] ngOnInit');

    // Reset chat state when "New Chat" is triggered from the sidebar,
    // including when we're already on '/' (route won't change, paramMap won't fire).
    this.newChatSub = this.sessionStorage.newChat$.subscribe(() => {
      this.authError.set(null);
      this.messages.set([]);
      this.messageActions.set(new Map());
      this.messageQueries.set(new Map());
      this.messageClarifications.set(new Map());
      this._pendingActions = [];
      this._pendingQueries = [];
      this._pendingClarifications = [];
      this.streamSub?.unsubscribe();
      this.stopReveal();
      this.streaming.set(false);
      this.lastFailedContent.set(null);
      this.chatLimitReached.set(false);
    });

    // ── Liquidation Monitor ──────────────────────────────────────────────
    // Start monitoring when wallet is connected; inject alerts as chat messages
    this.liquidationSub = this.liquidationMonitor.liquidationAlerts$.subscribe(alert => {
      const key = `${alert.protocol}:${alert.market}:${alert.severity}`;
      if (this._alertedKeys.has(key)) return; // already notified this session
      this._alertedKeys.add(key);

      const emoji = alert.severity === 'critical' ? '🚨' : alert.severity === 'danger' ? '⚠️' : '⚡';
      const injected: ChatMessage = {
        id: `liq-alert-${Date.now()}`,
        sessionId: this.sessionStorage.activeSessionId() ?? 'local',
        role: 'assistant',
        content: `${emoji} **Liquidation Alert — ${alert.protocol.toUpperCase()}**\n\nYour position on **${alert.market}** has a health ratio of **${(alert.healthRatio * 100).toFixed(1)}%** (threshold: ${(alert.threshold * 100).toFixed(0)}%). ${alert.message}\n\nWould you like me to add collateral or repay some debt to improve your health factor?`,
        createdAt: new Date().toISOString(),
      };
      this.messages.update(msgs => [...msgs, injected]);
    });

    if (this.walletService.connected()) {
      this.liquidationMonitor.startMonitoring(120_000); // check every 2 minutes
    }

    this.routeSub = this.route.paramMap.subscribe((params) => {
      const sessionId = params.get('sessionId');
      console.log('[ChatShell] Route params changed:', { sessionId });
      if (sessionId) {
        this.sessionStorage.setActiveSession(sessionId);
        // Don't reload messages while streaming — the route change from
        // session aliasing (local: → server id) would overwrite the active
        // streaming state and cause the UI to flash skeleton/hero.
        if (!this.streaming()) {
          this.loadMessages(sessionId);
        }
      } else {
        // Navigated to home — always clear error and messages
        this.authError.set(null);
        this.messages.set([]);
        this.messageActions.set(new Map());
        this.messageQueries.set(new Map());
        this.messageClarifications.set(new Map());
        this._pendingActions = [];
        this._pendingQueries = [];
        this._pendingClarifications = [];
        this.sessionStorage.setActiveSession(null);
        this.chatLimitReached.set(false);
      }
    });
  }

  ngOnDestroy(): void {
    this.streamSub?.unsubscribe();
    this.routeSub?.unsubscribe();
    this.newChatSub?.unsubscribe();
    this.liquidationSub?.unsubscribe();
    this.liquidationMonitor.stopMonitoring();
    this.stopReveal();
  }

  onRetry(): void {
    const content = this.lastFailedContent();
    if (!content) return;
    this.lastFailedContent.set(null);
    // Remove the last failed assistant message before retrying
    this.messages.update(msgs => {
      const updated = [...msgs];
      if (updated.length > 0 && updated[updated.length - 1].role === 'assistant') {
        updated.pop();
      }
      if (updated.length > 0 && updated[updated.length - 1].role === 'user') {
        updated.pop();
      }
      return updated;
    });
    this.onSendMessage(content);
  }

  exportChat(): void {
    const msgs = this.messages();
    if (msgs.length === 0) return;

    const lines: string[] = [`# OPRAI Chat Export`, `Exported: ${new Date().toLocaleString()}`, ''];
    for (const msg of msgs) {
      const role = msg.role === 'user' ? '**You**' : '**OPRAI**';
      const time = msg.createdAt ? new Date(msg.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
      lines.push(`### ${role}${time ? '  ·  ' + time : ''}`);
      lines.push(msg.content);
      lines.push('');
    }

    const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `oprai-chat-${Date.now()}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  clearChat(): void {
    this.streamSub?.unsubscribe();
    this.stopReveal();
    this.messages.set([]);
    this.messageActions.set(new Map());
    this.messageQueries.set(new Map());
    this.messageClarifications.set(new Map());
    this.streaming.set(false);
    this.lastFailedContent.set(null);
    // Navigate to root — route effect will clear the active session
    this.location.replaceState('/');
  }

  onSendMessage(content: string): void {
    if (!content.trim() || this.streaming()) return;
    this.lastFailedContent.set(null);

    // Get current attachments and clear them
    const attachments = this.currentAttachments();

    let sessionId = this.sessionStorage.activeSessionId();

    if (!sessionId) {
      const session = this.sessionStorage.createLocalSession(
        content.slice(0, 40)
      );
      sessionId = session.id;
    } else {
      // Touch existing session to move it to top of list
      this.sessionStorage.touchSession(sessionId);
    }

    const userMessage: ChatMessage = {
      id: `temp-${Date.now()}`,
      sessionId: sessionId,
      role: 'user',
      content,
      createdAt: new Date().toISOString(),
    };
    this.messages.update((msgs) => [...msgs, userMessage]);

    const assistantMessage: ChatMessage = {
      id: `temp-assistant-${Date.now()}`,
      sessionId: sessionId,
      role: 'assistant',
      content: '',
      createdAt: new Date().toISOString(),
      thinking: '',
    };
    this.messages.update((msgs) => [...msgs, assistantMessage]);

    this.streaming.set(true);
    this._revealBuffer = '';
    this._thinkingBuffer = '';
    this._streamDone = false;
    this._pendingActions = [];
    this._pendingQueries = [];
    this._pendingClarifications = [];
    this.currentThinking.set(null);
    this.startReveal();

    // Clear attachments after including in message
    this.currentAttachments.set([]);

    const resolvedSessionId = this.sessionStorage.resolveId(sessionId);

    this.streamSub = this.chatApi
      .sendMessageStream(resolvedSessionId, content, attachments.length > 0 ? attachments : undefined, false)
      .subscribe({
        next: (data) => {
          try {
            const parsed = JSON.parse(data);

            if (parsed.sessionId && sessionId!.startsWith('local:')) {
              this.sessionStorage.aliasSession(sessionId!, parsed.sessionId);
              this.location.replaceState('/c/' + parsed.sessionId);
            }

            if (parsed.title) {
              const resolvedId = this.sessionStorage.resolveId(sessionId!);
              this.sessionStorage.updateTitle(resolvedId, parsed.title);
              this.sessionStorage.updateTitle(sessionId!, parsed.title);
            }

            // Replace temp message ID with the real DB-assigned ID
            if (parsed.messageId) {
              this.messages.update((msgs) => {
                const updated = [...msgs];
                const last = updated[updated.length - 1];
                if (last.role === 'assistant') {
                  updated[updated.length - 1] = { ...last, id: parsed.messageId };
                }
                return updated;
              });
            }

            if (parsed.error) {
              this.stopReveal();
              this.flushRevealBuffer();
              this.currentThinking.set(null);

              // Chat limit: disable composer, don't show retry
              if (parsed.errorType === 'chat_limit') {
                this.chatLimitReached.set(true);
                this.streaming.set(false);
                // Remove the empty placeholder assistant message
                this.messages.update((msgs) => {
                  const updated = [...msgs];
                  const last = updated[updated.length - 1];
                  if (last.role === 'assistant' && !last.content) {
                    updated.pop();
                  }
                  return updated;
                });
                return;
              }

              // Regular error — existing handling
              this.lastFailedContent.set(content);
              this.messages.update((msgs) => {
                const updated = [...msgs];
                const last = updated[updated.length - 1];
                if (last.role === 'assistant') {
                  updated[updated.length - 1] = {
                    ...last,
                    content: parsed.error,
                    isError: true,
                  };
                }
                return updated;
              });
              return;
            }

            // ── Structured intent events from function calling ──────────
            // These are validated server-side — no regex parsing needed.
            if (parsed.action) {
              this._pendingActions.push(parsed.action as StructuredAction);
              return;
            }
            if (parsed.query) {
              this._pendingQueries.push(parsed.query as StructuredQuery);
              return;
            }
            if (parsed.clarify) {
              this._pendingClarifications.push(parsed.clarify as StructuredClarify);
              return;
            }

            // Show real thinking from LLM
            if (parsed.thinking) {
              this._thinkingBuffer += parsed.thinking;
              this.currentThinking.set(this._thinkingBuffer);
            }

            // When we receive actual content, clear thinking display
            if (parsed.delta || parsed.content) {
              this.currentThinking.set(null);
              const delta = parsed.delta ?? parsed.content ?? '';
              this._revealBuffer += delta;
            }
          } catch {
            this._revealBuffer += data;
          }
        },
        error: (err: Error) => {
          this.stopReveal();
          this.flushRevealBuffer();
          this.currentThinking.set(null);
          this.streaming.set(false);
          this.lastFailedContent.set(content);
          // Clear any partial structured data accumulated before the error
          this._pendingActions = [];
          this._pendingQueries = [];
          this._pendingClarifications = [];
          const errorMessage = err?.message ?? 'Connection error. Please try again.';
          this.messages.update((msgs) => {
            const updated = [...msgs];
            const last = updated[updated.length - 1];
            if (last.role === 'assistant' && !last.content) {
              updated[updated.length - 1] = {
                ...last,
                content: errorMessage,
                isError: true,
              };
            }
            return updated;
          });
        },
        complete: () => {
          this._streamDone = true;
          this.currentThinking.set(null);
          // The reveal timer will flush the rest and call finishStream()
        },
      });
  }

  onCancelAction(action: ParsedAction): void {
    const sessionId = this.sessionStorage.activeSessionId() ?? `local:${Date.now()}`;
    const msgId = `cancel-${Date.now()}`;
    const cancelLabel = action.type === 'cancel_limit_order' ? 'limit order' : 'DCA order';
    const msg: ChatMessage = {
      id: msgId,
      sessionId,
      role: 'assistant',
      content: `Cancel ${cancelLabel}:`,
      createdAt: new Date().toISOString(),
    };
    this.messages.update(msgs => [...msgs, msg]);
    this.messageActions.update(map => {
      const next = new Map(map);
      next.set(msgId, [action]);
      return next;
    });
  }

  /**
   * When user clicks a [CLARIFY] option:
   * Inject the selected option directly as an action-card and execute it.
   */
  onClarifySelected(action: ParsedAction): void {
    const sessionId = this.sessionStorage.activeSessionId() ?? `local:${Date.now()}`;
    const msgId = `clarify-action-${Date.now()}`;
    const msg: ChatMessage = {
      id: msgId,
      sessionId,
      role: 'assistant',
      content: '',
      createdAt: new Date().toISOString(),
    };
    this.messages.update(msgs => [...msgs, msg]);
    this.messageActions.update(map => {
      const next = new Map(map);
      next.set(msgId, [action]);
      return next;
    });
  }

  onFilesAttached(uploadResults: UploadResult[]): void {
    // Convert UploadResult[] to Attachment[]
    const attachments: Attachment[] = uploadResults.map(result => ({
      url: result.url,
      type: 'image' as const,
      filename: result.filename,
    }));
    this.currentAttachments.set(attachments);
  }

  // ── Typewriter reveal methods ──

  private startReveal(): void {
    if (this._revealTimer) return;
    this._revealTimer = setInterval(() => this.revealTick(), this.REVEAL_INTERVAL_MS);
  }

  private stopReveal(): void {
    if (this._revealTimer) {
      clearInterval(this._revealTimer);
      this._revealTimer = null;
    }
  }

  private revealTick(): void {
    if (this._revealBuffer.length === 0) {
      if (this._streamDone) {
        this.stopReveal();
        this.finishStream();
      }
      return;
    }

    // Reveal a few characters per tick for smooth, steady output
    const chars = this._streamDone
      ? Math.max(this.CHARS_PER_TICK, Math.ceil(this._revealBuffer.length / 10))
      : this.CHARS_PER_TICK;
    const chunk = this._revealBuffer.slice(0, chars);
    this._revealBuffer = this._revealBuffer.slice(chars);

    this.appendToLastAssistant(chunk);

    if (this._revealBuffer.length === 0 && this._streamDone) {
      this.stopReveal();
      this.finishStream();
    }
  }

  private flushRevealBuffer(): void {
    if (this._revealBuffer.length > 0) {
      this.appendToLastAssistant(this._revealBuffer);
      this._revealBuffer = '';
    }
  }

  private appendToLastAssistant(text: string): void {
    this.messages.update((msgs) => {
      const updated = [...msgs];
      const last = updated[updated.length - 1];
      if (last?.role === 'assistant') {
        updated[updated.length - 1] = { ...last, content: last.content + text };
      }
      return updated;
    });
  }

  private finishStream(): void {
    this.streaming.set(false);
    const msgs = this.messages();
    const lastMsg = msgs[msgs.length - 1];
    if (lastMsg?.role !== 'assistant') return;

    const hasStructured =
      this._pendingActions.length > 0 ||
      this._pendingQueries.length > 0 ||
      this._pendingClarifications.length > 0;

    if (hasStructured) {
      // ── Primary path: use validated structured events from function calling ──
      // Convert StructuredAction → ParsedAction shape that action-card expects.
      if (this._pendingActions.length > 0) {
        const parsedActions: ParsedAction[] = this._pendingActions.map(a => ({
          type: a.type,
          params: a.params,
          raw: '',
          chainFromPrevious: a.chainFromPrevious ?? false,
        }));
        this.messageActions.update(m => {
          const next = new Map(m);
          next.set(lastMsg.id, parsedActions);
          return next;
        });
      }
      if (this._pendingQueries.length > 0) {
        const parsedQueries: ParsedQuery[] = this._pendingQueries.map(q => ({
          type: q.type,
          params: q.params,
          raw: '',
        }));
        this.messageQueries.update(m => {
          const next = new Map(m);
          next.set(lastMsg.id, parsedQueries);
          return next;
        });
      }
      if (this._pendingClarifications.length > 0) {
        const parsedClarifications: ParsedClarify[] = this._pendingClarifications.map(c => ({
          category: c.category,
          question: c.question,
          options: c.options.map(o => ({
            label: o.label,
            sublabel: o.sublabel,
            action: o.action,
            params: o.params,
          })),
          raw: '',
        }));
        this.messageClarifications.update(m => {
          const next = new Map(m);
          next.set(lastMsg.id, parsedClarifications);
          return next;
        });
      }
      // Reset buffers
      this._pendingActions = [];
      this._pendingQueries = [];
      this._pendingClarifications = [];
    } else {
      // ── Fallback path: text-based parsing for historical messages ──
      // Used when loading old messages that pre-date function calling,
      // or for reasoning models that fall back to text streaming.
      const parsed = this.intentParser.parseAll(lastMsg.content);
      if (parsed.actions.length > 0) {
        this.messageActions.update(m => {
          const next = new Map(m);
          next.set(lastMsg.id, parsed.actions);
          return next;
        });
      }
      if (parsed.queries.length > 0) {
        this.messageQueries.update(m => {
          const next = new Map(m);
          next.set(lastMsg.id, parsed.queries);
          return next;
        });
      }
      if (parsed.clarifications.length > 0) {
        this.messageClarifications.update(m => {
          const next = new Map(m);
          next.set(lastMsg.id, parsed.clarifications);
          return next;
        });
      }
    }

    // Auto-summarize: fire-and-forget to memory service
    if (lastMsg.content.trim()) {
      const sessionId = this.sessionStorage.activeSessionId();
      const userMsg = msgs.length >= 2 ? msgs[msgs.length - 2] : null;
      const chunk = userMsg?.role === 'user'
        ? `User: ${userMsg.content}\nAssistant: ${lastMsg.content}`
        : `Assistant: ${lastMsg.content}`;

      void this.memoryService.summarize({
        conversation_id: sessionId ?? lastMsg.sessionId,
        chunk,
        token_count: Math.ceil(chunk.length / 4),
      });
    }
  }

  private loadMessages(sessionId: string): void {
    const resolved = this.sessionStorage.resolveId(sessionId);
    console.log('[ChatShell] loadMessages called:', { sessionId, resolved });

    if (resolved.startsWith('local:')) {
      console.log('[ChatShell] Local session, skipping message load');
      this.messages.set([]);
      this.loadingMessages.set(false);
      return;
    }

    // Check authentication first
    if (!this.authService.isAuthenticated()) {
      // If wallet is connected but JWT expired/cleared, try to re-authenticate silently
      if (this.walletService.publicKey() && !this.authService.authenticating()) {
        console.log('[ChatShell] Token missing but wallet connected — re-authenticating');
        this.authService.authenticate().subscribe({
          next: () => this.loadMessages(sessionId),
          error: () => {
            this.authError.set('Session expired. Please reconnect your wallet.');
            this.loadingMessages.set(false);
          },
        });
      } else if (!this.walletService.publicKey()) {
        this.authError.set('Please connect your wallet to view chat history');
        this.loadingMessages.set(false);
      }
      return;
    }

    // Wait if authentication is in progress
    if (this.authService.authenticating()) {
      console.log('[ChatShell] Authentication in progress, waiting...');
      return;
    }

    this.authError.set(null);
    this.loadingMessages.set(true);

    // Add timeout to prevent infinite loading
    const timeout = setTimeout(() => {
      console.warn('[ChatShell] Message load timeout');
      this.loadingMessages.set(false);
    }, 10000);

    this.chatApi.getMessages(resolved).subscribe({
      next: (msgs) => {
        clearTimeout(timeout);
        console.log('[ChatShell] Messages loaded:', msgs.length);
        this.messages.set(msgs);
        this.loadingMessages.set(false);

        const actionsMap = new Map<string, ParsedAction[]>();
        const queriesMap = new Map<string, ParsedQuery[]>();
        const clarifyMap = new Map<string, ParsedClarify[]>();

        for (const msg of msgs) {
          if (msg.role !== 'assistant') continue;
          const meta = msg.metadata;

          // Prefer structured metadata (function calling) over text parsing.
          // Text parsing is the fallback for messages that pre-date function calling.
          const structuredActions = meta?.actions;
          const structuredQueries = meta?.queries;
          const structuredClarifications = meta?.clarifications;

          if (structuredActions?.length) {
            actionsMap.set(msg.id, structuredActions.map(a => ({
              type: a.type, params: a.params, raw: '', chainFromPrevious: a.chainFromPrevious ?? false,
            })));
          }
          if (structuredQueries?.length) {
            queriesMap.set(msg.id, structuredQueries.map(q => ({
              type: q.type, params: q.params, raw: '',
            })));
          }
          if (structuredClarifications?.length) {
            clarifyMap.set(msg.id, structuredClarifications.map(c => ({
              category: c.category, question: c.question,
              options: c.options.map(o => ({
                label: o.label, sublabel: o.sublabel, action: o.action, params: o.params,
              })),
              raw: '',
            })));
          }

          // Fall back to text parsing only if no structured metadata
          if (!structuredActions?.length && !structuredQueries?.length && !structuredClarifications?.length) {
            const parsed = this.intentParser.parseAll(msg.content);
            if (parsed.actions.length > 0) actionsMap.set(msg.id, parsed.actions);
            if (parsed.queries.length > 0) queriesMap.set(msg.id, parsed.queries);
            if (parsed.clarifications.length > 0) clarifyMap.set(msg.id, parsed.clarifications);
          }
        }
        this.messageActions.set(actionsMap);
        this.messageQueries.set(queriesMap);
        this.messageClarifications.set(clarifyMap);
      },
      error: (err) => {
        clearTimeout(timeout);
        console.error('[ChatShell] Failed to load messages:', err);
        this.loadingMessages.set(false);
        if (err.message === 'Authentication required' || err.status === 401) {
          this.authError.set('Session expired. Please reconnect your wallet.');
        } else if (err.status === 404) {
          // Session doesn't exist for this wallet — remove stale local entry and go home
          this.sessionStorage.removeSession(sessionId);
          this.router.navigate(['/']);
        }
      },
    });
  }

  /**
   * Execute a list of actions sequentially — each action runs only after
   * the previous one succeeds. Stops and throws on first failure.
   */
  async executeActionsSequentially(
    actions: ParsedAction[],
    callbacks: ActionCallbacks = {},
  ): Promise<string[]> {
    const results: string[] = [];
    for (const action of actions) {
      const validationErrors = this.intentParser.validateActionParams(action.type, action.params);
      if (validationErrors.length > 0) {
        throw new Error(`Invalid action params for "${action.type}": ${validationErrors.join('; ')}`);
      }
      const result = await this.solanaActionService.execute(action, callbacks);
      results.push(result);
    }
    return results;
  }

  /**
   * Send a transaction failure back to the LLM as user context so it can
   * suggest recovery alternatives.
   */
  sendFollowUpError(actionType: string, errorMessage: string): void {
    const sessionId = this.sessionStorage.activeSessionId();
    if (!sessionId) return;
    const errorContext =
      `TRANSACTION_FAILED: ${actionType} — ${errorMessage}. ` +
      `Please diagnose the issue and suggest an alternative approach.`;
    this.onSendMessage(errorContext);
  }
}
