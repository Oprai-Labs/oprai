import { Component, inject, signal, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { Subscription } from 'rxjs';
import { RiskWarningService, RiskWarningPayload } from '@core/services/risk-warning.service';

@Component({
  selector: 'app-risk-warning-dialog',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  templateUrl: './risk-warning-dialog.component.html',
  styleUrl: './risk-warning-dialog.component.scss',
})
export class RiskWarningDialogComponent implements OnInit, OnDestroy {
  private readonly riskWarning = inject(RiskWarningService);

  readonly payload = signal<RiskWarningPayload | null>(null);
  /** Ticked the "I checked the address" box. Reset per dialog. */
  readonly ack = signal(false);
  readonly copied = signal(false);
  private copyTimer?: ReturnType<typeof setTimeout>;

  private sub?: Subscription;

  ngOnInit(): void {
    this.sub = this.riskWarning.warning$.subscribe(p => {
      this.ack.set(false);
      this.copied.set(false);
      this.payload.set(p);
    });
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
  }

  /** True while the confirm button must stay out of reach. */
  blocked(): boolean {
    const p = this.payload();
    return !!p?.requireAck && !this.ack();
  }

  toggleAck(): void { this.ack.update(v => !v); }

  /** First four and last four characters — enough to compare against a source. */
  shortMint(mint: string): string {
    return mint.length > 12 ? `${mint.slice(0, 6)}…${mint.slice(-6)}` : mint;
  }

  async copyMint(mint: string, ev: Event): Promise<void> {
    ev.stopPropagation();
    try { await navigator.clipboard.writeText(mint); } catch { return; }
    this.copied.set(true);
    clearTimeout(this.copyTimer);
    this.copyTimer = setTimeout(() => this.copied.set(false), 1400);
  }

  confirm(): void {
    const p = this.payload();
    if (!p || this.blocked()) return;
    p.resolve(true);
    this.payload.set(null);
  }

  cancel(): void {
    const p = this.payload();
    if (!p) return;
    p.resolve(false);
    this.payload.set(null);
  }
}
