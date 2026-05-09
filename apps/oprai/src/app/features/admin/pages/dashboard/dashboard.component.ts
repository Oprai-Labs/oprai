import { Component, inject, OnInit, signal } from '@angular/core';
import { AdminApiService, DashboardStats } from '../../services/admin-api.service';
import { AdminLayoutComponent } from '../../components/admin-layout/admin-layout.component';
import { SkeletonStatCardsComponent } from '@shared/components/skeletons/skeleton-stat-cards.component';
import { SkeletonChartComponent } from '@shared/components/skeletons/skeleton-chart.component';
import { EmptyStateComponent } from '@shared/components/empty-state/empty-state.component';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [
    AdminLayoutComponent,
    SkeletonStatCardsComponent, SkeletonChartComponent, EmptyStateComponent,
  ],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.scss',
})
export class DashboardComponent implements OnInit {
  private readonly adminApi = inject(AdminApiService);

  readonly stats = signal<DashboardStats | null>(null);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  ngOnInit(): void {
    this.loadStats();
  }

  logout(): void {
    this.adminApi.adminLogout().subscribe({
      complete: () => { window.location.href = '/admin/login'; },
      error: () => { window.location.href = '/admin/login'; },
    });
  }

  private loadStats(): void {
    this.loading.set(true);
    this.adminApi.getDashboardStats().subscribe({
      next: (stats) => {
        this.stats.set(stats);
        this.loading.set(false);
      },
      error: (_err) => {
        this.error.set('Failed to load dashboard stats');
        this.loading.set(false);
      },
    });
  }
}
