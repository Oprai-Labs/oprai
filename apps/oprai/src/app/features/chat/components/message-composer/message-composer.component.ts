import {
  Component,
  Input,
  Output,
  EventEmitter,
  ViewChild,
  ElementRef,
  AfterViewInit,
  signal,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { LucideAngularModule } from 'lucide-angular';
import { UploadService, UploadResult } from '@core/services/upload.service';

export interface AttachedFile {
  file: File;
  uploadResult?: UploadResult;
  uploading: boolean;
  error?: string;
}

@Component({
  selector: 'app-message-composer',
  standalone: true,
  imports: [CommonModule, FormsModule, LucideAngularModule],
  templateUrl: './message-composer.component.html',
  styleUrl: './message-composer.component.scss',
})
export class MessageComposerComponent implements AfterViewInit {
  @Input() disabled = false;
  @Input() chatLimitReached = false;
  @Output() send = new EventEmitter<string>();
  @Output() filesAttached = new EventEmitter<UploadResult[]>();

  @ViewChild('textareaRef') textareaRef!: ElementRef<HTMLTextAreaElement>;
  @ViewChild('fileInput') fileInputRef!: ElementRef<HTMLInputElement>;

  private readonly uploadService = inject(UploadService);

  readonly MAX_INPUT_CHARS = 2000;

  message = '';
  readonly attachedFiles = signal<AttachedFile[]>([]);
  readonly plusMenuOpen = signal(false);
  readonly activeMode = signal<'thinking' | 'search' | null>(null);

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

        // Upload immediately
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
    // Only emit successfully uploaded files
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
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.onSend();
    }
  }

  onSend(): void {
    const trimmed = this.message.trim();
    if (!trimmed || this.disabled || this.chatLimitReached || this.isOverLimit) return;

    this.send.emit(trimmed);
    this.message = '';
    this.attachedFiles.set([]);
    this.resetTextareaHeight();
    this.focusInput();
  }

  onInput(): void {
    this.autoResize();
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
    // Google Drive integration placeholder
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
