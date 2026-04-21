import { Component, Input, Output, EventEmitter } from '@angular/core';
import type { PortfolioTab } from '../../models/portfolio.models';

@Component({
  selector: 'app-portfolio-tabs',
  standalone: true,
  templateUrl: './portfolio-tabs.component.html',
  styleUrl: './portfolio-tabs.component.scss',
})
export class PortfolioTabsComponent {
  @Input() activeTab: PortfolioTab = 'tokens';
  @Output() tabChange = new EventEmitter<PortfolioTab>();

  readonly tabs: Array<{ id: PortfolioTab; label: string }> = [
    { id: 'tokens', label: 'Tokens' },
    { id: 'defi', label: 'DeFi' },
    { id: 'nfts', label: 'NFTs' },
    { id: 'history', label: 'History' },
  ];

  selectTab(tab: PortfolioTab): void {
    this.tabChange.emit(tab);
  }
}
