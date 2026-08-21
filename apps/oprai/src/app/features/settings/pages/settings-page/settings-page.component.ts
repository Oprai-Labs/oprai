import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { AccountCardComponent } from '../../components/account-card/account-card.component';
import { UsageCardComponent } from '../../components/usage-card/usage-card.component';
import { ThemeSwitcherComponent } from '../../components/theme-switcher/theme-switcher.component';
import { PrivacyCardComponent } from '../../components/privacy-card/privacy-card.component';
import { TPipe } from '@core/i18n';

@Component({
  selector: 'app-settings-page',
  standalone: true,
  imports: [CommonModule,
    AccountCardComponent,
    UsageCardComponent,
    ThemeSwitcherComponent,
    PrivacyCardComponent, TPipe],
  templateUrl: './settings-page.component.html',
  styleUrl: './settings-page.component.scss',
})
export class SettingsPageComponent {}
