import { Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { firstValueFrom } from 'rxjs';
import { LucideAngularModule } from 'lucide-angular';
import { ApiService } from '@core/services/api.service';
import { SessionStorageService } from '@core/services/session-storage.service';

type ConfirmTarget = 'history' | 'memories' | null;

@Component({
  selector: 'app-privacy-card',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  templateUrl: './privacy-card.component.html',
  styleUrl: './privacy-card.component.scss',
})
export class PrivacyCardComponent {
  private readonly api = inject(ApiService);
  private readonly sessionStorage = inject(SessionStorageService);

  readonly confirming = signal<ConfirmTarget>(null);
  readonly busy = signal<ConfirmTarget>(null);
  readonly message = signal<{ kind: 'ok' | 'error'; text: string } | null>(null);

  ask(target: Exclude<ConfirmTarget, null>): void {
    this.confirming.set(target);
    this.message.set(null);
  }

  cancel(): void {
    this.confirming.set(null);
  }

  async confirmDeleteHistory(): Promise<void> {
    if (this.busy()) return;
    this.busy.set('history');
    try {
      const res = await firstValueFrom(
        this.api.delete<{ deleted: number }>('/sessions/all'),
      );
      this.sessionStorage.clear();
      this.message.set({
        kind: 'ok',
        text: `Deleted ${res?.deleted ?? 0} conversation${res?.deleted === 1 ? '' : 's'}.`,
      });
    } catch (err) {
      this.message.set({
        kind: 'error',
        text: err instanceof Error ? err.message : 'Failed to delete chat history.',
      });
    } finally {
      this.busy.set(null);
      this.confirming.set(null);
    }
  }

  async confirmDeleteMemories(): Promise<void> {
    if (this.busy()) return;
    this.busy.set('memories');
    try {
      const res = await firstValueFrom(
        this.api.delete<{ deleted: number }>('/user/memories'),
      );
      this.message.set({
        kind: 'ok',
        text: `Deleted ${res?.deleted ?? 0} memor${res?.deleted === 1 ? 'y' : 'ies'}.`,
      });
    } catch (err) {
      this.message.set({
        kind: 'error',
        text: err instanceof Error ? err.message : 'Failed to delete memories.',
      });
    } finally {
      this.busy.set(null);
      this.confirming.set(null);
    }
  }
}
