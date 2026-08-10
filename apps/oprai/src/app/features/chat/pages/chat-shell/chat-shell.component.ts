import { Component, inject, signal, effect, untracked, OnInit, OnDestroy, Injector, ViewChild } from '@angular/core';
import { CommonModule, Location } from '@angular/common';
import { ActivatedRoute, Router } from '@angular/router';
import { Observable, Subscription } from 'rxjs';
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
import { MessageComposerComponent, SendEvent } from '../../components/message-composer/message-composer.component';
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

  /** True once the cookie-based restore has finished, so "not authenticated"
   *  means signed out rather than not-yet-known. Without it the boot moment —
   *  when both flags are false — reads as a failed sign-in. */
  private _authSettled = false;
  readonly authError = signal<string | null>(null);
  readonly currentThinking = signal<string | null>(null);
  readonly chatLimitReached = signal(false);
  /**
   * When the backend rejects a request because a daily wallet cap was hit,
   * the error payload carries the cap details so we can render an accurate
   * "you used X of Y, resets at Z" banner instead of the generic
   * "conversation limit reached" line. Null when no cap has been hit
   * or when the limit is a conversation-level one (chat_limit), in which
   * case the generic banner copy is the right wording.
   */
  readonly capInfo = signal<{
    unit: 'messages' | 'tokens';
    used: number;
    cap: number;
    resetsAt: string;
  } | null>(null);
  readonly messageActions = signal<Map<string, ParsedAction[]>>(new Map());
  readonly messageQueries = signal<Map<string, ParsedQuery[]>>(new Map());
  readonly messageClarifications = signal<Map<string, ParsedClarify[]>>(new Map());
  /**
   * Map of `${clarifyMessageId}:${clarifyIndex}` → selected option index, plus
   * a back-pointer of the spawned action's message id so we can clear the
   * selection if the user cancels the action card.
   */
  readonly clarifySelections = signal<Map<string, number>>(new Map());
  private readonly actionToClarify = new Map<string, string>(); // actionMsgId → clarifyKey
  private readonly solanaActionService = inject(SolanaActionService);
  private readonly liquidationMonitor = inject(LiquidationMonitorService);
  private readonly injector = inject(Injector);
  readonly currentAttachments = signal<Attachment[]>([]);
  readonly initialLoading = signal(true);
  /** Content of the last failed message — shown with a Retry button. */
  readonly lastFailedContent = signal<string | null>(null);
  /** Latest content the user sent. Captured at send time so `finishStream()`
   * can offer Retry/Edit when the LLM returns nothing (empty stream). */
  private _lastSentContent: string | null = null;
  private _lastSentProtocols: string[] = [];
  private streamSub?: Subscription;
  private msgSub?: Subscription;       // in-flight getMessages subscription
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

    // The cookie restore decides whether "signed out" is a fact or just a
    // moment that hasn't resolved. Only after it lands can a signed-out state
    // be reported as one.
    void this.authService.whenAuthReady().finally(() => { this._authSettled = true; });

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
      this.capInfo.set(null);
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

    // Retry message loading when auth state becomes valid after a race condition.
    // Handles the case where loadMessages returned early because auth was still
    // in progress, leaving the skeleton visible with no API call underway.
    // Must pass injector: ngOnInit is not an injection context in Angular 19.
    effect(() => {
      const isAuthenticated = this.authService.isAuthenticated();
      const isAuthenticating = this.authService.authenticating();
      if (isAuthenticated && !isAuthenticating) {
        untracked(() => {
          if (this.authError()) {
            this.authError.set(null);
          }
          const sessionId = this.sessionStorage.activeSessionId();
          // Retry if we have a session, no messages loaded, and no request in flight.
          // loadingMessages may be false if the first attempt returned early (race with auth).
          if (sessionId && this.messages().length === 0 && !this.msgSub) {
            this.loadingMessages.set(true);
            this.loadMessages(sessionId);
          }
        });
      } else if (!isAuthenticated && !isAuthenticating && this._authSettled) {
        // Sign-in finished and we are STILL signed out: the attempt failed, or
        // the wallet never answered. Nothing is coming, and a skeleton is a
        // promise that something is — this is the state the app sat in forever
        // after a signature request that went unanswered.
        untracked(() => {
          if (!this.loadingMessages() || this.authError()) return;
          this.loadingMessages.set(false);
          this.authError.set(
            this.walletService.publicKey()
              ? 'Your wallet hasn\'t approved the sign-in request yet. Open your wallet, approve it, then try again.'
              : 'Connect your wallet to open this conversation.',
          );
        });
      }
    }, { injector: this.injector });

    this.routeSub = this.route.paramMap.subscribe((params) => {
      const sessionId = params.get('sessionId');
      console.log('[ChatShell] Route params changed:', { sessionId });
      if (sessionId) {
        this.sessionStorage.setActiveSession(sessionId);

        // Cancel any in-flight stream or messages request from a previous session.
        if (this.streaming()) {
          this.streamSub?.unsubscribe();
          this.stopReveal();
          this._revealBuffer = '';
          this._streamDone = false;
          this.streaming.set(false);
          this.currentThinking.set(null);
          this._pendingActions = [];
          this._pendingQueries = [];
          this._pendingClarifications = [];
        }
        this.msgSub?.unsubscribe();
        this.msgSub = undefined;

        // Reset state for the new session and show skeleton immediately.
        this.messages.set([]);
        this.messageActions.set(new Map());
        this.messageQueries.set(new Map());
        this.messageClarifications.set(new Map());
        this.lastFailedContent.set(null);
        this.authError.set(null);
        this.loadingMessages.set(true);  // Show skeleton; loadMessages may keep or clear it
        this.loadMessages(sessionId);
      } else {
        // Navigated to home — cancel any pending request and clear state.
        this.msgSub?.unsubscribe();
        this.msgSub = undefined;
        this.authError.set(null);
        this.loadingMessages.set(false);
        this.messages.set([]);
        this.messageActions.set(new Map());
        this.messageQueries.set(new Map());
        this.messageClarifications.set(new Map());
        this._pendingActions = [];
        this._pendingQueries = [];
        this._pendingClarifications = [];
        this.sessionStorage.setActiveSession(null);
        this.chatLimitReached.set(false);
      this.capInfo.set(null);
      }
    });
  }

  ngOnDestroy(): void {
    this.streamSub?.unsubscribe();
    this.msgSub?.unsubscribe();
    this.routeSub?.unsubscribe();
    this.newChatSub?.unsubscribe();
    this.liquidationSub?.unsubscribe();
    this.liquidationMonitor.stopMonitoring();
    this.stopReveal();
  }

  retryLoad(): void {
    const sessionId = this.sessionStorage.activeSessionId();
    // Signed out is the common reason to be here, and it has nothing to do with
    // a session. Returning early left the button doing nothing at all on a
    // fresh tab, which is where a failed sign-in most often lands.
    if (!this.authService.isAuthenticated() && this.walletService.publicKey()) {
      this.authError.set(null);
      this.authService.authenticate().subscribe({
        next: () => { if (sessionId) this.retryLoad(); },
        error: (err: unknown) => this.authError.set(
          (err as { message?: string })?.message
            || 'Sign-in failed. Open your wallet, approve the request, and try again.',
        ),
      });
      return;
    }
    if (!sessionId) return;
    this.authError.set(null);
    this.msgSub?.unsubscribe();
    this.msgSub = undefined;
    this.loadingMessages.set(true);
    this.loadMessages(sessionId);
  }

  /** The conversation hit its length limit — open a fresh one. */
  onStartNewChat(): void {
    this.sessionStorage.triggerNewChat();
  }

  onRetry(): void {
    const content = this.lastFailedContent();
    if (!content) return;
    this.lastFailedContent.set(null);
    // Remove the last failed assistant message before retrying. Capture the
    // dropped IDs so we can also evict their inline mini-apps (action card,
    // query card, clarify card) — otherwise stale QueryCard / ActionCard
    // entries linger in the per-message maps and re-render against the new
    // assistant turn that reuses similar IDs.
    const droppedIds: string[] = [];
    this.messages.update(msgs => {
      const updated = [...msgs];
      if (updated.length > 0 && updated[updated.length - 1].role === 'assistant') {
        droppedIds.push(updated.pop()!.id);
      }
      if (updated.length > 0 && updated[updated.length - 1].role === 'user') {
        droppedIds.push(updated.pop()!.id);
      }
      return updated;
    });
    this._evictMessageMiniApps(droppedIds);
    this.onSendMessage(content);
  }

  /**
   * "Edit message" affordance on a failed assistant turn — pop the failed
   * assistant + user pair off the message list, drop the original text into
   * the composer so the user can tweak phrasing/parameters and send again.
   * No automatic re-send — the user controls when it goes.
   */
  onEditLast(): void {
    const content = this.lastFailedContent();
    if (!content) return;
    this.lastFailedContent.set(null);
    const droppedIds: string[] = [];
    this.messages.update(msgs => {
      const updated = [...msgs];
      if (updated.length > 0 && updated[updated.length - 1].role === 'assistant') {
        droppedIds.push(updated.pop()!.id);
      }
      if (updated.length > 0 && updated[updated.length - 1].role === 'user') {
        droppedIds.push(updated.pop()!.id);
      }
      return updated;
    });
    this._evictMessageMiniApps(droppedIds);
    // Defer to next tick so the message-list rerender finishes before the
    // composer takes focus — otherwise the focus jumps around.
    queueMicrotask(() => this.composer?.setText(content));
  }

  /** Drop ActionCard / QueryCard / ClarifyCard entries owned by the given
   *  message ids. Used by the retry / edit-last paths so popped messages
   *  don't leave behind orphan mini-apps in the per-message maps. */
  private _evictMessageMiniApps(ids: string[]): void {
    if (!ids.length) return;
    const evict = (map: Map<string, unknown>) => {
      const next = new Map(map);
      for (const id of ids) next.delete(id);
      return next;
    };
    this.messageActions.update(m => evict(m) as typeof m);
    this.messageQueries.update(m => evict(m) as typeof m);
    this.messageClarifications.update(m => evict(m) as typeof m);
    // Also purge the LOCALSTORAGE-persisted client action cards for these ids —
    // otherwise a regenerate/edit clears them from view but `restoreClientActions`
    // re-adds them on the next message reload (the bug: regenerating an earlier
    // turn left the Raydium mini-apps from a later, discarded turn on screen).
    for (const id of ids) this.removeClientAction(id);
  }

  /** Human-readable copy for an llm_daily_cap event. Rendered inside the
   *  assistant bubble so the user sees the reason without having to look
   *  at the composer banner at the bottom of the page. */
  private _formatCapMessage(
    unit: 'messages' | 'tokens',
    used: number,
    cap: number,
    resetsAtIso: string,
  ): string {
    const noun     = unit === 'tokens' ? 'token' : 'message';
    const usedFmt  = used.toLocaleString('en-US');
    const capFmt   = cap.toLocaleString('en-US');
    const target   = Date.parse(resetsAtIso);
    let resetIn    = 'a moment';
    if (Number.isFinite(target) && target > Date.now()) {
      const totalSec = Math.max(1, Math.round((target - Date.now()) / 1000));
      const h = Math.floor(totalSec / 3600);
      const m = Math.floor((totalSec % 3600) / 60);
      resetIn = h > 0
        ? (m > 0 ? `${h}h ${m}m` : `${h}h`)
        : `${m}m`;
    }
    return `Daily ${noun} limit reached (${usedFmt} of ${capFmt}). Resets in ${resetIn}.`;
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

  onSendMessage(event: SendEvent | string): void {
    const content = typeof event === 'string' ? event : event.content;
    const protocols = typeof event === 'string' ? [] : (event.protocols ?? []);
    if (!content.trim() || this.streaming()) return;
    this.lastFailedContent.set(null);
    this._lastSentContent = content;
    this._lastSentProtocols = protocols;

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
      // Mirror the @-tags the composer sent, so the chip renders on the
      // bubble immediately — not only after a page reload pulls it back
      // from `metadata.protocols` in the DB.
      ...(protocols.length > 0 ? { metadata: { protocols: [...protocols] } } : {}),
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

    this.streamSub = this._subscribeStream(
      this.chatApi.sendMessageStream(
        resolvedSessionId, content,
        attachments.length > 0 ? attachments : undefined,
        false,
        protocols.length > 0 ? protocols : undefined,
      ),
      content,
      sessionId!,
    );
  }

  /**
   * Edit a previous user message and stream a fresh assistant turn.
   *
   * Local cleanup must happen BEFORE we open the SSE: the user has already
   * decided that everything from the edited message onward is gone, so the
   * UI removes those rows immediately. The backend mirrors this by stamping
   * `metadata.superseded_at` on the same range and creating a new user
   * message with the edited content. After that, the stream pipeline is
   * identical to a normal send — same parser, same reveal logic, same
   * error handling.
   */
  onEditMessage(payload: { messageId: string; content: string }): void {
    const trimmed = payload.content.trim();
    if (!trimmed || this.streaming()) return;

    const sessionId = this.sessionStorage.activeSessionId();
    if (!sessionId) return;

    // If the target message ID is a temp/placeholder (failed first turn never
    // got a DB row, so userMessageId never came back over SSE) OR the session
    // is still local-only, the backend's `/messages/edit` will 400 on
    // `uuid.UUID(temp-…)`. Fall back to a regular send so the user actually
    // gets a fresh turn instead of a silent no-op.
    const _UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    const isTempId = !_UUID_RE.test(payload.messageId);
    if (sessionId.startsWith('local:') || isTempId) {
      // Drop the existing turn from local state (same UX as a real edit) so
      // the retry doesn't pile on top, then run the normal send path which
      // creates a new persisted turn.
      const all = this.messages();
      const editIdx = all.findIndex(m => m.id === payload.messageId);
      if (editIdx !== -1) {
        const droppedIds = all.slice(editIdx).map(m => m.id);
        this.messages.set(all.slice(0, editIdx));
        this._evictMessageMiniApps(droppedIds);
      }
      this.onSendMessage(trimmed);
      return;
    }

    // Drop the edited message + every later one. We do NOT splice mid-list
    // and resend; chat semantics demand the user sees their edit replace
    // the original cleanly, with no orphaned assistant reply lingering.
    const all = this.messages();
    const editIdx = all.findIndex(m => m.id === payload.messageId);
    if (editIdx === -1) return;
    const truncated = all.slice(0, editIdx);
    // Purge persisted client action cards (Deposit/clarify-pick mini-apps) that
    // belonged to the discarded tail, so `restoreClientActions` can't resurrect
    // them after the edited turn re-streams.
    for (const m of all.slice(editIdx)) this.removeClientAction(m.id);

    // Reuse the same protocols the user originally tagged on this message
    // (if any) — the backend won't re-derive them and the chip metadata
    // disappears with the soft-delete unless we forward it explicitly.
    const originalProtocols = (all[editIdx].metadata?.protocols ?? []) as string[];

    const newUserMessage: ChatMessage = {
      id: `temp-${Date.now()}`,
      sessionId,
      role: 'user',
      content: trimmed,
      createdAt: new Date().toISOString(),
      metadata: originalProtocols.length > 0
        ? { protocols: originalProtocols, edited_at: new Date().toISOString() }
        : { edited_at: new Date().toISOString() },
    };
    const newAssistant: ChatMessage = {
      id: `temp-assistant-${Date.now()}`,
      sessionId,
      role: 'assistant',
      content: '',
      createdAt: new Date().toISOString(),
      thinking: '',
    };
    this.messages.set([...truncated, newUserMessage, newAssistant]);

    // Drop cached structured intent / clarify state for messages that no
    // longer exist — otherwise stale cards leak into the next turn.
    const survivingIds = new Set(truncated.map(m => m.id));
    survivingIds.add(newUserMessage.id);
    survivingIds.add(newAssistant.id);
    this.messageActions.update(map => {
      const next = new Map(map);
      for (const k of next.keys()) if (!survivingIds.has(k)) next.delete(k);
      return next;
    });
    this.messageQueries.update(map => {
      const next = new Map(map);
      for (const k of next.keys()) if (!survivingIds.has(k)) next.delete(k);
      return next;
    });
    this.messageClarifications.update(map => {
      const next = new Map(map);
      for (const k of next.keys()) if (!survivingIds.has(k)) next.delete(k);
      return next;
    });

    this.streaming.set(true);
    this._revealBuffer = '';
    this._thinkingBuffer = '';
    this._streamDone = false;
    this._pendingActions = [];
    this._pendingQueries = [];
    this._pendingClarifications = [];
    this.currentThinking.set(null);
    this.startReveal();

    const resolvedSessionId = this.sessionStorage.resolveId(sessionId);
    this.streamSub = this._subscribeStream(
      this.chatApi.editMessageStream(
        resolvedSessionId,
        payload.messageId,
        trimmed,
        originalProtocols.length > 0 ? originalProtocols : undefined,
      ),
      trimmed,
      sessionId,
    );
  }

  /**
   * Shared SSE handler used by both the regular send and the edit-resend
   * paths. Centralising the subscribe block here avoids 150 lines of
   * duplication and keeps reveal/thinking/error semantics in one spot.
   */
  private _subscribeStream(
    stream$: Observable<string>,
    content: string,
    sessionId: string,
  ): Subscription {
    return stream$.subscribe({
        next: (data) => {
          try {
            const parsed = JSON.parse(data);

            // Edit-mode preamble: server confirms which message was edited
            // and how many messages it superseded. The local cleanup already
            // ran before the SSE opened, so this is just for diagnostics — we
            // swallow it here so it doesn't fall through to the reveal buffer.
            if (parsed.edit) {
              return;
            }

            if (parsed.sessionId && sessionId!.startsWith('local:')) {
              this.sessionStorage.aliasSession(sessionId!, parsed.sessionId);
              this.location.replaceState('/c/' + parsed.sessionId);
              // Update sessionId in every in-memory message so action-card.persistResult
              // sends the real UUID (not "local:...") to the backend PATCH endpoint.
              this.messages.update(msgs =>
                msgs.map(m => m.sessionId === sessionId ? { ...m, sessionId: parsed.sessionId } : m)
              );
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

            // Same idea for the user-side message — without this, the temp
            // `temp-${Date.now()}` id stays forever, and any subsequent edit
            // on the same bubble POSTs that fake id to /messages/edit, where
            // the UUID parser rejects it and the user sees a generic error.
            // Real id is yielded by stream_chat_response right after the
            // user message is flushed.
            if (parsed.userMessageId) {
              this.messages.update((msgs) => {
                const updated = [...msgs];
                // Walk back from the end: the last message is the assistant
                // placeholder, the one before it is the user message we just
                // sent. (No structural assumption beyond that.)
                for (let i = updated.length - 1; i >= 0; i--) {
                  if (updated[i].role === 'user' && updated[i].id.startsWith('temp-')) {
                    updated[i] = { ...updated[i], id: parsed.userMessageId };
                    break;
                  }
                }
                return updated;
              });
            }

            if (parsed.error) {
              this.stopReveal();
              this.flushRevealBuffer();
              this.currentThinking.set(null);

              // Chat limit (conversation-level OR per-wallet daily cap):
              // disable composer, don't show retry. The daily-cap variant
              // ships extra fields (unit/used/cap/resetsAt) so the composer
              // banner can render an accurate "resets in X hours" message.
              if (parsed.errorType === 'chat_limit' || parsed.errorType === 'llm_daily_cap') {
                this.chatLimitReached.set(true);
                let capMessage: string;
                let isConversationLimit = false;
                if (parsed.errorType === 'llm_daily_cap' && parsed.unit && parsed.cap && parsed.resetsAt) {
                  this.capInfo.set({
                    unit:     parsed.unit,
                    used:     parsed.used ?? 0,
                    cap:      parsed.cap,
                    resetsAt: parsed.resetsAt,
                  });
                  capMessage = this._formatCapMessage(
                    parsed.unit,
                    parsed.used ?? 0,
                    parsed.cap,
                    parsed.resetsAt,
                  );
                  isConversationLimit = false;
                } else {
                  this.capInfo.set(null);
                  capMessage = 'This conversation has reached its limit. Start a new chat to continue.';
                  // Retrying or editing re-sends into the same full conversation
                  // and fails identically — the only way forward is a new chat.
                  isConversationLimit = true;
                }
                this.streaming.set(false);
                // Replace the empty placeholder assistant message with the
                // cap notice so the user sees what happened directly in the
                // chat thread — not only in the composer's bottom banner.
                this.messages.update((msgs) => {
                  const updated = [...msgs];
                  const last = updated[updated.length - 1];
                  if (last?.role === 'assistant') {
                    updated[updated.length - 1] = {
                      ...last,
                      content: capMessage,
                      isError: true,
                      isConversationLimit,
                    };
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
              // Re-enable composer so the user can retry / type a new message.
              // The backend already terminated the stream with [DONE]; without
              // this the composer stays locked in "streaming" state until the
              // user hits retry (which sends a fresh request).
              this.streaming.set(false);
              this._pendingActions = [];
              this._pendingQueries = [];
              this._pendingClarifications = [];
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
              // `replace: true` means this delta supersedes everything
              // streamed so far (server-side override of a hedge / junk
              // reply). Drop the un-revealed buffer AND wipe the already-
              // revealed bubble text before appending the corrected answer.
              if (parsed.replace) {
                this._revealBuffer = '';
                this.resetLastAssistant();
              }
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
          // Map raw error to a friendly user-facing message. Auth errors stay
          // explicit; everything else collapses to a generic line so we don't
          // leak stack traces / status codes into the chat UI.
          const rawMessage = err?.message ?? '';
          const isAuth = /authentication error|401|unauthor/i.test(rawMessage);
          const isNetwork = /network|fetch|cors|timeout|connection|refused|unreachable|abort/i.test(rawMessage);
          const isRate = /rate.?limit|429|too many requests/i.test(rawMessage);
          const friendly = isAuth
            ? 'Your session expired. Please sign in again.'
            : isRate
              ? 'OPRAI is busy right now. Please try again in a moment.'
              : isNetwork
                ? 'Connection issue. Please check your network and try again.'
                : "Sorry, something went wrong. Please try again.";
          this.messages.update((msgs) => {
            const updated = [...msgs];
            const last = updated[updated.length - 1];
            if (last.role === 'assistant' && !last.content) {
              updated[updated.length - 1] = {
                ...last,
                content: friendly,
                isError: true,
              };
            }
            return updated;
          });
          // If the failure was an auth error, the cookie is gone — clear local user
          // state so the sidebar/UI reflect the unauthenticated state. Without this
          // the user keeps seeing their old session list while every request 401s.
          if (isAuth) {
            this.authService.logout();
          }
        },
        complete: () => {
          this._streamDone = true;
          this.currentThinking.set(null);
          // The reveal timer will flush the rest and call finishStream()
        },
      });
  }

  @ViewChild(MessageComposerComponent) private composer?: MessageComposerComponent;

  /** User clicked an inline `:protocol:` mention in an LLM reply — forward it
   * to the composer so the protocol becomes a chip, just like an @ pick. */
  onProtocolMention(protocolId: string): void {
    this.composer?.addProtocolById(protocolId);
  }

  onCancelAction(action: ParsedAction): void {
    const sessionId = this.sessionStorage.activeSessionId() ?? `local:${Date.now()}`;
    const msgId = `cancel-${Date.now()}`;
    // The lead-in text must match the action, not default everything to "DCA".
    let leadIn: string;
    switch (action.type) {
      case 'cancel_limit_order':      leadIn = 'Cancel limit order:'; break;
      case 'cancel_all_limit_orders': leadIn = 'Cancel all limit orders:'; break;
      case 'cancel_dca':              leadIn = 'Cancel DCA order:'; break;
      case 'perp_close':              leadIn = 'Close position:'; break;
      default:                        leadIn = 'Confirm:'; break;
    }
    const msg: ChatMessage = {
      id: msgId,
      sessionId,
      role: 'assistant',
      content: leadIn,
      createdAt: new Date().toISOString(),
    };
    this.messages.update(msgs => [...msgs, msg]);
    this.messageActions.update(map => {
      const next = new Map(map);
      next.set(msgId, [action]);
      return next;
    });
    this.persistClientAction(sessionId, msgId, action, msg.createdAt);
  }

  /**
   * Spawn a fresh action card from a row-level "Use this pool" CTA inside a
   * QueryCard. Mirrors the structure used by `onClarifySelected`: append a
   * new assistant message with empty content + attach the parsed action so
   * the message-list renders it as a normal action-card the user can fill
   * out and submit.
   */
  onUseAction(payload: { sourceMessageId: string; action: ParsedAction }): void {
    const { action } = payload;
    const sessionId = this.sessionStorage.activeSessionId() ?? `local:${Date.now()}`;

    // A card can emit a QUERY as well as an action — "Browse listings" wants
    // the listings themselves, not a form asking which collection. This used
    // to make an action card regardless, so the button answered a question the
    // user had already answered by clicking it: a two-field form pre-filled
    // with the collection they were looking at, and a Confirm to press.
    if (action.raw?.startsWith('[QUERY:')) {
      const queryMsgId = `use-query-${Date.now()}`;
      this.messages.update(msgs => [...msgs, {
        id: queryMsgId,
        sessionId,
        role: 'assistant' as const,
        content: '',
        createdAt: new Date().toISOString(),
      }]);
      this.messageQueries.update(map => {
        const next = new Map(map);
        next.set(queryMsgId, [{ type: action.type, params: action.params, raw: action.raw }]);
        return next;
      });
      return;
    }

    const actionMsgId = `use-action-${Date.now()}`;
    const msg: ChatMessage = {
      id: actionMsgId,
      sessionId,
      role: 'assistant',
      content: '',
      createdAt: new Date().toISOString(),
    };
    this.messages.update(msgs => [...msgs, msg]);
    this.messageActions.update(map => {
      const next = new Map(map);
      next.set(actionMsgId, [action]);
      return next;
    });
    this.persistClientAction(sessionId, actionMsgId, action, msg.createdAt);
  }

  // ── Client-generated action cards persistence ──────────────────────────────
  // Deposit/Withdraw/Multiply buttons on a query-card (and clarify picks) create
  // an action card that is NOT a server message. Persist it per-session in
  // localStorage so a page reload — which reloads messages from the DB — doesn't
  // drop it (with its cachedResult the card even restores its tx status).
  private clientActionsKey(sessionId: string): string { return `client-actions:${sessionId}`; }

  private persistClientAction(sessionId: string, msgId: string, action: ParsedAction, createdAt: string): void {
    try {
      const key = this.clientActionsKey(sessionId);
      const list: any[] = JSON.parse(localStorage.getItem(key) ?? '[]');
      list.push({ msgId, action, createdAt });
      // Bound the list so it can't grow forever on a long-lived chat.
      localStorage.setItem(key, JSON.stringify(list.slice(-50)));
    } catch { /* storage full / disabled — non-fatal */ }
  }

  private removeClientAction(msgId: string): void {
    try {
      const sessionId = this.sessionStorage.activeSessionId();
      if (!sessionId) return;
      const key = this.clientActionsKey(sessionId);
      const list: any[] = JSON.parse(localStorage.getItem(key) ?? '[]');
      const next = list.filter(x => x.msgId !== msgId);
      if (next.length !== list.length) localStorage.setItem(key, JSON.stringify(next));
      // Drop any persisted results for this card too.
      const rKey = `client-action-results:${sessionId}`;
      const rMap: Record<string, any> = JSON.parse(localStorage.getItem(rKey) ?? '{}');
      let changed = false;
      for (const k of Object.keys(rMap)) {
        if (k.startsWith(`${msgId}::`)) { delete rMap[k]; changed = true; }
      }
      if (changed) localStorage.setItem(rKey, JSON.stringify(rMap));
    } catch { /* non-fatal */ }
  }

  private restoreClientActions(sessionId: string): void {
    try {
      const list: any[] = JSON.parse(localStorage.getItem(this.clientActionsKey(sessionId)) ?? '[]');
      if (!list.length) return;
      // Results the cards persisted (tx signature / confirmed status) live in a
      // sibling store keyed by `${msgId}::${resultKey}` — fold them back into
      // each restored message's metadata so the card shows its completed state.
      const resultsMap: Record<string, any> = JSON.parse(
        localStorage.getItem(`client-action-results:${sessionId}`) ?? '{}',
      );
      const existing = this.messageActions();
      const toAdd: ChatMessage[] = [];
      const actionEntries: { id: string; action: ParsedAction }[] = [];
      for (const item of list) {
        if (!item?.msgId || !item?.action || existing.has(item.msgId)) continue;
        actionEntries.push({ id: item.msgId, action: item.action });
        const prefix = `${item.msgId}::`;
        const actionResults: Record<string, any> = {};
        for (const [k, v] of Object.entries(resultsMap)) {
          if (k.startsWith(prefix)) actionResults[k.slice(prefix.length)] = v;
        }
        toAdd.push({
          id: item.msgId, sessionId, role: 'assistant', content: '',
          createdAt: item.createdAt ?? new Date(0).toISOString(),
          ...(Object.keys(actionResults).length ? { metadata: { action_results: actionResults } } : {}),
        } as ChatMessage);
      }
      if (!toAdd.length) return;
      this.messageActions.update(map => {
        const nx = new Map(map);
        for (const e of actionEntries) nx.set(e.id, [e.action]);
        return nx;
      });
      // Merge + keep chronological order so restored cards sit after their
      // originating query-card, not jumbled at the top.
      this.messages.update(msgs => [...msgs, ...toAdd].sort(
        (a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime(),
      ));
    } catch { /* non-fatal */ }
  }

  /**
   * When user clicks a [CLARIFY] option:
   * Inject the selected option directly as an action-card. We also remember
   * which clarify message + option this action came from, so cancelling the
   * action card can re-enable the original clarify-card for a re-pick.
   */
  onClarifySelected(payload: { messageId: string; clarifyIndex: number; optionIndex: number; action: ParsedAction }): void {
    const { messageId: clarifyMsgId, clarifyIndex, optionIndex, action } = payload;
    const clarifyKey = `${clarifyMsgId}:${clarifyIndex}`;
    const sessionId = this.sessionStorage.activeSessionId() ?? `local:${Date.now()}`;

    // "Başka bir çift" (specify another pair) on a Raydium CLMM add-liquidity
    // clarify: the option spawns raydium_open_position with NO pool and NO
    // token pair. Rather than make the user pick two tokens blind — where any
    // combo can turn out to have no pool — route straight to the pool SEARCH
    // list (all CLMM pools, TVL-sorted, each row a real pool with a Deposit
    // CTA). Preset pairs (SOL/USDC …) carry tokenA/tokenB and are unaffected.
    if (
      action.type === 'raydium_open_position' &&
      !action.params?.['poolId'] &&
      !(action.params?.['tokenA'] && action.params?.['tokenB'])
    ) {
      const query: ParsedQuery = {
        type: 'raydium_search_pools',
        params: { poolType: 'concentrated' },
        raw: '[QUERY:raydium_search_pools]',
      };
      const queryMsgId = `clarify-query-${Date.now()}`;
      const qmsg: ChatMessage = {
        id: queryMsgId,
        sessionId,
        role: 'assistant',
        content: '',
        createdAt: new Date().toISOString(),
      };
      this.messages.update(msgs => [...msgs, qmsg]);
      this.messageQueries.update(map => {
        const next = new Map(map);
        next.set(queryMsgId, [query]);
        return next;
      });
      this.clarifySelections.update(map => {
        const next = new Map(map);
        next.set(clarifyKey, optionIndex);
        return next;
      });
      return;
    }

    const actionMsgId = `clarify-action-${Date.now()}`;
    const msg: ChatMessage = {
      id: actionMsgId,
      sessionId,
      role: 'assistant',
      content: '',
      createdAt: new Date().toISOString(),
    };
    this.messages.update(msgs => [...msgs, msg]);
    this.messageActions.update(map => {
      const next = new Map(map);
      next.set(actionMsgId, [action]);
      return next;
    });
    this.persistClientAction(sessionId, actionMsgId, action, msg.createdAt);
    this.clarifySelections.update(map => {
      const next = new Map(map);
      next.set(clarifyKey, optionIndex);
      return next;
    });
    this.actionToClarify.set(actionMsgId, clarifyKey);
  }

  /**
   * User clicked Cancel on an action-card. If the action came from a clarify
   * pick, remove the action card and clear the originating clarify's selection
   * so the user can pick a different protocol. Server-stored actions (regular
   * LLM responses) just get the action card removed locally.
   */
  onActionDismissed(payload: { messageId: string; action: ParsedAction }): void {
    const { messageId } = payload;
    const clarifyKey = this.actionToClarify.get(messageId);

    // Drop the action(s) for this message and the message itself if it was
    // a synthetic clarify-action shell (no real content).
    this.messageActions.update(map => {
      const next = new Map(map);
      next.delete(messageId);
      return next;
    });
    // Client-generated shells (clarify pick, query-card Deposit/Withdraw, cancel)
    // have no real content — drop the message AND its localStorage record so a
    // dismissed card doesn't reappear on reload.
    if (messageId.startsWith('clarify-action-') || messageId.startsWith('use-action-') || messageId.startsWith('cancel-')) {
      this.messages.update(msgs => msgs.filter(m => m.id !== messageId));
      this.removeClientAction(messageId);
    }

    if (clarifyKey) {
      this.clarifySelections.update(map => {
        const next = new Map(map);
        next.delete(clarifyKey);
        return next;
      });
      this.actionToClarify.delete(messageId);
    }
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

  /** Wipe the already-revealed text of the in-flight assistant bubble.
   *  Used when a `replace: true` delta supersedes a hedge / junk reply. */
  private resetLastAssistant(): void {
    this.messages.update((msgs) => {
      const updated = [...msgs];
      const last = updated[updated.length - 1];
      if (last?.role === 'assistant') {
        updated[updated.length - 1] = { ...last, content: '' };
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
      // DATA_ONLY actions (no TX, pure data) are auto-executed inline — no action card shown.
      if (this._pendingActions.length > 0) {
        const dataOnlyActions: StructuredAction[] = [];
        const txActions: StructuredAction[] = [];
        for (const a of this._pendingActions) {
          if (SolanaActionService.DATA_ONLY_TYPES.has(a.type)) {
            dataOnlyActions.push(a);
          } else {
            txActions.push(a);
          }
        }

        // Auto-execute data-only actions and append their result to the message text.
        for (const a of dataOnlyActions) {
          const action: ParsedAction = {
            type: a.type, params: a.params, raw: '',
            chainFromPrevious: false, warnUnverifiedDestination: false,
          };
          this.solanaActionService.executeChain([action], {
            onConfirm: (result?: string) => {
              if (!result) return;
              this.messages.update(msgs => {
                const updated = [...msgs];
                const idx = updated.findIndex(m => m.id === lastMsg.id);
                if (idx !== -1) {
                  const existing = updated[idx].content;
                  updated[idx] = {
                    ...updated[idx],
                    content: existing ? `${existing}\n\n${result}` : result,
                  };
                }
                return updated;
              });
            },
          }).catch(() => { /* silent — error already logged by service */ });
        }

        if (txActions.length > 0) {
          const parsedActions: ParsedAction[] = txActions.map(a => ({
            type: a.type,
            params: a.params,
            raw: '',
            chainFromPrevious: a.chainFromPrevious ?? false,
            warnUnverifiedDestination: a.warnUnverifiedDestination ?? false,
          }));
          this.messageActions.update(m => {
            const next = new Map(m);
            next.set(lastMsg.id, parsedActions);
            return next;
          });
        }
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
            icon: o.icon,
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

    // Empty-response detection: stream completed cleanly but the assistant
    // produced no text and no structured action/query/clarify. The user sees
    // a blank bubble with no recourse — show the same retry/edit affordances
    // we offer for explicit errors. Most common cause: LLM emitted only
    // pre-tool buffer that got discarded, or the model decided not to call
    // any tool and also produced no text (e.g. tool-list filtered to a set
    // it couldn't satisfy). Failsafe — never the user's fault.
    const attachedActions = this.messageActions().get(lastMsg.id) ?? [];
    const attachedQueries = this.messageQueries().get(lastMsg.id) ?? [];
    const attachedClarifs = this.messageClarifications().get(lastMsg.id) ?? [];
    const hasAnyContent =
      lastMsg.content.trim().length > 0 ||
      attachedActions.length > 0 ||
      attachedQueries.length > 0 ||
      attachedClarifs.length > 0;
    if (!hasAnyContent && !lastMsg.isError && this._lastSentContent) {
      this.lastFailedContent.set(this._lastSentContent);
      this.messages.update((msgs) => {
        const updated = [...msgs];
        const last = updated[updated.length - 1];
        if (last?.role === 'assistant') {
          updated[updated.length - 1] = {
            ...last,
            content: "Sorry, I couldn't generate a response. Please try again.",
            isError: true,
          };
        }
        return updated;
      });
      return; // skip auto-summarize for the empty turn
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
      console.log('[ChatShell] Local session (unsynced), skipping message load:', resolved);
      this.messages.set([]);
      this.loadingMessages.set(false);
      // Don't show an error for the initial empty local session state
      return;
    }

    // Check authentication — re-authenticate if we have a wallet but no session.
    if (!this.authService.isAuthenticated()) {
      if (this.walletService.publicKey() && !this.authService.authenticating()) {
        console.log('[ChatShell] Token missing but wallet connected — re-authenticating');
        // loadingMessages stays true (skeleton visible while signing)
        this.authService.authenticate().subscribe({
          next: () => this.loadMessages(sessionId),
          error: () => {
            this.authError.set('Your session has ended. Sign in again to continue.');
            this.loadingMessages.set(false);
          },
        });
      } else if (!this.walletService.publicKey()) {
        this.authError.set('Connect your wallet to open this conversation.');
        this.loadingMessages.set(false);
      }
      // else: authenticating() is true — skeleton stays, effect retries when done
      return;
    }

    // Auth is in progress from elsewhere — skeleton stays, effect retries.
    if (this.authService.authenticating()) {
      console.log('[ChatShell] Authentication in progress, waiting...');
      return;
    }

    this.authError.set(null);
    this.loadingMessages.set(true);

    const timeout = setTimeout(() => {
      console.warn('[ChatShell] Message load timeout');
      this.loadingMessages.set(false);
      this.authError.set('Taking longer than usual. Tap to retry.');
    }, 10000);

    // Store subscription so in-flight requests can be cancelled on navigation.
    this.msgSub = this.chatApi.getMessages(resolved).subscribe({
      next: (resp) => {
        clearTimeout(timeout);
        const msgs = resp.messages;
        console.log('[ChatShell] Messages loaded:', msgs.length, 'isLocked=', resp.session.isLocked);
        this.messages.set(msgs ?? []);
        this.loadingMessages.set(false);

        // Per-chat lock survives reloads — re-apply it now so the composer
        // stays disabled on this conversation no matter how many times the
        // user refreshes.
        if (resp.session.isLocked) {
          this.chatLimitReached.set(true);
          this.capInfo.set(null);
        } else {
          this.chatLimitReached.set(false);
          this.capInfo.set(null);
        }

        const actionsMap = new Map<string, ParsedAction[]>();
        const queriesMap = new Map<string, ParsedQuery[]>();
        const clarifyMap = new Map<string, ParsedClarify[]>();

        for (const msg of msgs ?? []) {
          if (msg.role !== 'assistant') continue;
          const meta = msg.metadata;

          const structuredActions = meta?.actions;
          const structuredQueries = meta?.queries;
          const structuredClarifications = meta?.clarifications;

          if (structuredActions?.length) {
            actionsMap.set(msg.id, structuredActions.map(a => ({
              type: a.type, params: a.params, raw: '',
              chainFromPrevious: a.chainFromPrevious ?? false,
              warnUnverifiedDestination: a.warnUnverifiedDestination ?? false,
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
                label: o.label, sublabel: o.sublabel, icon: o.icon,
                action: o.action, params: o.params,
              })),
              raw: '',
            })));
          }

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
        // Client-generated action cards (query-card Deposit/Withdraw/Multiply,
        // clarify picks) live only in the browser — they aren't server messages,
        // so restore them from localStorage or they vanish on reload.
        this.restoreClientActions(resolved);
      },
      error: (err) => {
        clearTimeout(timeout);
        console.error('[ChatShell] Failed to load messages:', { status: err.status, message: err.message, url: err.url, err });
        this.loadingMessages.set(false);
        if (err.message === 'Authentication required' || err.status === 401) {
          // Cookie expired but wallet still connected → silently re-authenticate
          // (one wallet-sign popup) instead of forcing a full logout/disconnect.
          // The user sees the same conversation reload after they sign.
          if (this.walletService.publicKey() && !this.authService.authenticating()) {
            this.authService.clearLocalAuth();
            this.loadingMessages.set(true);
            this.authService.authenticate().subscribe({
              next: () => this.loadMessages(sessionId),
              error: () => {
                this.authError.set('Your session has ended. Sign in again to continue.');
                this.loadingMessages.set(false);
                this.messages.set([]);
              },
            });
          } else {
            this.authError.set('Your session has ended. Sign in again to continue.');
            this.authService.logout();
            this.messages.set([]);
          }
        } else if (err.status === 403) {
          this.authError.set('You don\'t have access to this conversation. Reconnect your wallet to continue.');
          this.authService.logout();
          this.messages.set([]); // drop any cached messages from the prior wallet
        } else if (err.status === 404) {
          // Session not found on this wallet — most common cause is a wallet
          // switch where the old session belongs to the previous wallet. Clear
          // any messages still in the view so we don't keep showing them while
          // the error banner says "not available".
          this.authError.set('This conversation is no longer available.');
          this.sessionStorage.removeSession(sessionId);
          this.messages.set([]);
        } else if (err.status === 0) {
          // Status 0 = network unreachable / CORS / offline. Don't expose
          // internal "services are running" wording — that's a dev concern.
          this.authError.set('Connection lost. Check your internet and try again.');
        } else if (err.status >= 500) {
          this.authError.set('We\'re having trouble right now. Please try again in a moment.');
        } else {
          this.authError.set('Couldn\'t load this conversation. Tap to retry.');
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
