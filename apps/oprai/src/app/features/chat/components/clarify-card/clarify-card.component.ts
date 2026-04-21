import {
  Component,
  Input,
  Output,
  EventEmitter,
  ChangeDetectionStrategy,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import { ParsedClarify, ClarifyOption } from '../../services/intent-parser.service';
import { ParsedAction } from '../../services/intent-parser.service';
import { ProtocolRegistryService, ProtocolInfo } from '@core/services/protocol-registry.service';

@Component({
  selector: 'app-clarify-card',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  templateUrl: './clarify-card.component.html',
  styleUrls: ['./clarify-card.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ClarifyCardComponent {
  @Input({ required: true }) clarify!: ParsedClarify;

  private readonly protocolRegistry = inject(ProtocolRegistryService);

  /** Execute as Action immediately when user clicks an option */
  @Output() actionSelected = new EventEmitter<ParsedAction>();

  /** Selected option index (for UI feedback) */
  selectedIndex: number | null = null;

  readonly categoryIcons: Record<string, string> = {
    stake: 'layers',
    lend: 'banknote',
    borrow: 'credit-card',
    liquidity: 'droplets',
    swap: 'arrow-left-right',
    perp: 'trending-up',
    nft: 'image',
    bridge: 'globe',
  };

  icon(): string {
    return this.categoryIcons[this.clarify.category] ?? 'help-circle';
  }

  /**
   * Get protocol info for an option (for displaying icons, colors, etc.)
   */
  getProtocolInfo(option: ClarifyOption): ProtocolInfo | undefined {
    // Try to extract protocol ID from the action name
    const action = option.action;
    if (action.includes('_')) {
      const protocol = action.split('_')[0];
      return this.protocolRegistry.getProtocol(protocol);
    }
    return undefined;
  }

  /**
   * Check if an option has protocol metadata
   */
  hasProtocolInfo(option: ClarifyOption): boolean {
    return !!this.getProtocolInfo(option);
  }

  select(option: ClarifyOption, index: number): void {
    if (this.selectedIndex !== null) return; // Already selected
    this.selectedIndex = index;

    // Convert selected option to Action and notify parent
    const action: ParsedAction = {
      type: option.action,
      params: option.params,
      raw: `[ACTION:${option.action}] ${JSON.stringify(option.params)}`,
    };
    this.actionSelected.emit(action);
  }
}
