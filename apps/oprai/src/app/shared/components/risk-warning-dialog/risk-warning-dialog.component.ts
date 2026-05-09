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

  private sub?: Subscription;

  ngOnInit(): void {
    this.sub = this.riskWarning.warning$.subscribe(p => {
      this.payload.set(p);
    });
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
  }

  confirm(): void {
    const p = this.payload();
    if (!p) return;
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
