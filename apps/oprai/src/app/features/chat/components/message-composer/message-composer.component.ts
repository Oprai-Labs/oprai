import {
  Component,
  Input,
  Output,
  EventEmitter,
  ViewChild,
  ElementRef,
  AfterViewInit,
  signal,
  computed,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { LucideAngularModule } from 'lucide-angular';
import { UploadService, UploadResult } from '@core/services/upload.service';
import { PROTOCOLS, Protocol } from '../../models/protocol-list';
import { TPipe } from '@core/i18n';

export interface AttachedFile {
  file: File;
  uploadResult?: UploadResult;
  uploading: boolean;
  error?: string;
}

export interface SendEvent {
  content: string;
  protocols: string[];
}

export interface CapInfo {
  unit: 'messages' | 'tokens';
  used: number;
  cap: number;
  resetsAt: string;
}

@Component({
  selector: 'app-message-composer',
  standalone: true,
  imports: [CommonModule, FormsModule, LucideAngularModule, TPipe],
  templateUrl: './message-composer.component.html',
  styleUrl: './message-composer.component.scss',
})
export class MessageComposerComponent implements AfterViewInit {
  @Input() disabled = false;
  @Input() chatLimitReached = false;
  @Input() capInfo: CapInfo | null = null;
  @Output() send = new EventEmitter<SendEvent>();
  @Output() filesAttached = new EventEmitter<UploadResult[]>();

  /**
   * Human-friendly banner copy for the limit-reached state.
   * - When a per-wallet daily cap is hit, render the unit, the used/cap
   *   fraction, and a relative reset time ("resets in 4h 12m").
   * - When the conversation-level chat_limit is hit (no capInfo payload),
   *   fall back to the generic "start a new chat" wording.
   */
  get limitBannerMessage(): string {
    if (!this.capInfo) {
      return 'Conversation limit reached. Start a new chat to continue.';
    }
    const { unit, used, cap, resetsAt } = this.capInfo;
    const noun = unit === 'tokens' ? 'token' : 'message';
    const usedFmt = used.toLocaleString('en-US');
    const capFmt  = cap.toLocaleString('en-US');
    return `Daily ${noun} limit reached (${usedFmt} of ${capFmt}). Resets in ${this.formatResetIn(resetsAt)}.`;
  }

  /** Short relative duration like "4h 12m", "47m", "12s". */
  private formatResetIn(resetsAtIso: string): string {
    const now    = Date.now();
    const target = Date.parse(resetsAtIso);
    if (!Number.isFinite(target) || target <= now) return 'a moment';
    const totalSec = Math.max(1, Math.round((target - now) / 1000));
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
    if (m > 0) return s > 30 ? `${m + 1}m` : `${m}m`;
    return `${s}s`;
  }

  @ViewChild('textareaRef') textareaRef!: ElementRef<HTMLTextAreaElement>;
  @ViewChild('fileInput') fileInputRef!: ElementRef<HTMLInputElement>;

  private readonly uploadService = inject(UploadService);

  readonly MAX_INPUT_CHARS = 2000;
  message = '';
  readonly attachedFiles = signal<AttachedFile[]>([]);
  readonly plusMenuOpen = signal(false);
  readonly activeMode = signal<'thinking' | 'search' | null>(null);

  // Protocol picker state
  readonly selectedProtocols = signal<Protocol[]>([]);
  readonly pickerOpen = signal(false);
  readonly pickerQuery = signal('');
  readonly pickerActiveIndex = signal(0);
  private atStartIndex = -1;

  readonly filteredProtocols = computed(() => {
    const q = this.pickerQuery().toLowerCase();
    const selected = new Set(this.selectedProtocols().map(p => p.id));
    return PROTOCOLS.filter(p =>
      !selected.has(p.id) &&
      (p.label.toLowerCase().includes(q) ||
       p.id.toLowerCase().includes(q) ||
       p.category.toLowerCase().includes(q))
    );
  });

  get charCount(): number { return this.message.length; }
  get isOverLimit(): boolean { return this.message.length >= this.MAX_INPUT_CHARS; }
  get showCounter(): boolean { return this.message.length >= Math.floor(this.MAX_INPUT_CHARS * 0.7); }

  ngAfterViewInit(): void {
    this.focusInput();
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files) {
      const newFiles = Array.from(input.files);

      for (const file of newFiles) {
        const attachedFile: AttachedFile = {
          file,
          uploading: true,
        };
        this.attachedFiles.update(files => [...files, attachedFile]);

        this.uploadService.uploadImage(file).subscribe({
          next: (result) => {
            this.attachedFiles.update(files =>
              files.map(f =>
                f.file === file
                  ? { ...f, uploadResult: result, uploading: false }
                  : f
              )
            );
            this.emitUploadedFiles();
          },
          error: (err) => {
            this.attachedFiles.update(files =>
              files.map(f =>
                f.file === file
                  ? { ...f, uploading: false, error: err.message || 'Upload failed' }
                  : f
              )
            );
          },
        });
      }

      input.value = '';
    }
  }

  removeFile(attachedFile: AttachedFile): void {
    this.attachedFiles.update(files => files.filter(f => f !== attachedFile));
    this.emitUploadedFiles();
  }

  private emitUploadedFiles(): void {
    const uploadedResults = this.attachedFiles()
      .filter(f => f.uploadResult)
      .map(f => f.uploadResult!);
    this.filesAttached.emit(uploadedResults);
  }

  get hasUploadingFiles(): boolean {
    return this.attachedFiles().some(f => f.uploading);
  }

  get hasUploadErrors(): boolean {
    return this.attachedFiles().some(f => f.error);
  }

  onKeydown(event: KeyboardEvent): void {
    if (this.pickerOpen()) {
      const filtered = this.filteredProtocols();
      switch (event.key) {
        case 'ArrowDown':
          event.preventDefault();
          this.pickerActiveIndex.update(i => Math.min(i + 1, filtered.length - 1));
          break;
        case 'ArrowUp':
          event.preventDefault();
          this.pickerActiveIndex.update(i => Math.max(i - 1, 0));
          break;
        case 'Enter':
          event.preventDefault();
          if (filtered[this.pickerActiveIndex()]) {
            this.selectProtocol(filtered[this.pickerActiveIndex()]);
          }
          break;
        case 'Escape':
          event.preventDefault();
          this.closePicker();
          break;
        case 'Backspace':
          // let through — onInput handles picker query update
          break;
        default:
          break;
      }
      return;
    }

    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.onSend();
    }
  }

  onInput(): void {
    this.autoResize();

    const textarea = this.textareaRef?.nativeElement;
    if (!textarea) return;

    const val = textarea.value;
    const cursor = textarea.selectionStart ?? val.length;

    if (this.pickerOpen()) {
      // Find the @ that opened the picker
      const afterAt = val.substring(this.atStartIndex + 1, cursor);
      if (this.atStartIndex >= 0 && val[this.atStartIndex] === '@') {
        // Still have @ before cursor with no space — update query
        if (!afterAt.includes(' ') && !afterAt.includes('\n')) {
          this.pickerQuery.set(afterAt);
          this.pickerActiveIndex.set(0);
          return;
        }
      }
      // Space or deleted past @ — close picker
      this.closePicker();
      return;
    }

    // Detect fresh @ keystroke
    if (val[cursor - 1] === '@') {
      this.atStartIndex = cursor - 1;
      this.pickerQuery.set('');
      this.pickerActiveIndex.set(0);
      this.pickerOpen.set(true);
    }
  }

  selectProtocol(proto: Protocol): void {
    // Remove @query text from textarea
    const textarea = this.textareaRef?.nativeElement;
    if (textarea && this.atStartIndex >= 0) {
      const cursor = textarea.selectionStart ?? this.message.length;
      this.message =
        this.message.substring(0, this.atStartIndex) +
        this.message.substring(cursor);
    }

    this.selectedProtocols.update(p => [...p, proto]);
    this.closePicker();

    setTimeout(() => {
      textarea?.focus();
      const pos = this.atStartIndex >= 0 ? this.atStartIndex : this.message.length;
      textarea?.setSelectionRange(pos, pos);
    });
  }

  removeProtocol(proto: Protocol): void {
    this.selectedProtocols.update(p => p.filter(x => x.id !== proto.id));
  }

  /**
   * Add a protocol to the chip row from outside the composer (e.g. when the
   * user clicks an inline `:protocol:` mention rendered inside an LLM reply).
   * Idempotent: silently no-ops if the protocol is unknown or already chosen.
   * Focuses the textarea so the user can type a follow-up question right away.
   */
  addProtocolById(id: string): void {
    const proto = PROTOCOLS.find(p => p.id === id);
    if (!proto) return;
    if (this.selectedProtocols().some(p => p.id === id)) {
      this.focusInput();
      return;
    }
    this.selectedProtocols.update(p => [...p, proto]);
    this.focusInput();
  }

  /**
   * Populate the composer with the text the user previously sent so they can
   * edit and resend. Used by the "Edit message" affordance on failed
   * assistant responses (ChatGPT-style recovery).
   */
  setText(text: string, protocols?: string[]): void {
    this.message = text ?? '';
    if (protocols && protocols.length > 0) {
      const matched = PROTOCOLS.filter(p => protocols.includes(p.id));
      this.selectedProtocols.set(matched);
    }
    this.focusInput();
    // Defer autoResize to next microtask so the textarea has the new value.
    queueMicrotask(() => this.autoResize());
  }

  closePicker(): void {
    this.pickerOpen.set(false);
    this.pickerQuery.set('');
    this.atStartIndex = -1;
  }

  onSend(): void {
    const trimmed = this.message.trim();
    if (!trimmed || this.disabled || this.chatLimitReached || this.isOverLimit) return;

    this.send.emit({
      content: trimmed,
      protocols: this.selectedProtocols().map(p => p.id),
    });
    this.message = '';
    this.attachedFiles.set([]);
    this.selectedProtocols.set([]);
    this.closePicker();
    this.resetTextareaHeight();
    this.focusInput();
  }

  private autoResize(): void {
    const textarea = this.textareaRef?.nativeElement;
    if (!textarea) return;
    textarea.style.height = 'auto';
    const maxHeight = 200;
    textarea.style.height = Math.min(textarea.scrollHeight, maxHeight) + 'px';
  }

  private resetTextareaHeight(): void {
    const textarea = this.textareaRef?.nativeElement;
    if (textarea) {
      textarea.style.height = 'auto';
    }
  }

  // ── Plus Menu ──

  togglePlusMenu(): void {
    this.plusMenuOpen.update(v => !v);
  }

  closePlusMenu(): void {
    this.plusMenuOpen.set(false);
  }

  onAddFiles(): void {
    this.closePlusMenu();
    this.fileInputRef?.nativeElement?.click();
  }

  onAddFromDrive(): void {
    this.closePlusMenu();
  }

  toggleWebResearch(): void {
    this.activeMode.update(v => v === 'search' ? null : 'search');
    this.closePlusMenu();
  }

  toggleThinking(): void {
    this.activeMode.update(v => v === 'thinking' ? null : 'thinking');
    this.closePlusMenu();
  }

  clearActiveMode(): void {
    this.activeMode.set(null);
  }

  private focusInput(): void {
    setTimeout(() => {
      this.textareaRef?.nativeElement?.focus();
    });
  }
}
