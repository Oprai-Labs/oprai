import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { WalletButtonComponent } from '@shared/components/wallet-button/wallet-button.component';
import { ThemeToggleComponent } from '@shared/components/theme-toggle/theme-toggle.component';
import { TPipe } from '@core/i18n';

interface NavLink {
  label: string;
  icon: string;
  route: string;
  exact: boolean;
}

@Component({
  selector: 'app-header-bar',
  standalone: true,
  imports: [CommonModule, RouterLink, RouterLinkActive,
    LucideAngularModule, WalletButtonComponent, ThemeToggleComponent, TPipe],
  templateUrl: './header-bar.component.html',
  styleUrl: './header-bar.component.scss',
})
export class HeaderBarComponent {
  readonly navLinks: NavLink[] = [
    { label: 'Chat', icon: 'message-square', route: '/', exact: true },
    { label: 'Market', icon: 'bar-chart-3', route: '/market', exact: false },
    { label: 'Trade', icon: 'arrow-right-left', route: '/trade', exact: false },
    { label: 'Portfolio', icon: 'portfolio', route: '/portfolio', exact: false },
    { label: 'Agents', icon: 'tesseract', route: '/agents', exact: false },
  ];
}
