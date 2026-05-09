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

  /**
   * Currently-selected option index, owned by the parent. When the parent
   * resets it to `null` (e.g. after the user cancels the action that this
   * selection spawned), the buttons re-enable so a different protocol can
   * be picked.
   */
  @Input() selectedIndex: number | null = null;

  private readonly protocolRegistry = inject(ProtocolRegistryService);

  /** Emit which option the user clicked, plus the parsed action to execute. */
  @Output() optionSelected = new EventEmitter<{ index: number; action: ParsedAction }>();

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

  /** Card title is context-aware — picking a pool is not the same as picking
   *  a protocol, and the LLM-supplied `category` already encodes intent. The
   *  fallback "Select Option" is a safe English default; this card only ships
   *  English UI strings, while the LLM-generated `question`/`label`/`sublabel`
   *  follow the user's language. */
  private readonly categoryTitles: Record<string, string> = {
    liquidity: 'Select Pool',
    stake: 'Select Validator',
    lend: 'Select Market',
    borrow: 'Select Market',
    swap: 'Select Route',
    perp: 'Select Market',
    nft: 'Select Collection',
    bridge: 'Select Route',
  };

  icon(): string {
    return this.categoryIcons[this.clarify.category] ?? 'help-circle';
  }

  title(): string {
    return this.categoryTitles[this.clarify.category] ?? 'Select Option';
  }

  /**
   * Action-prefix → registry-id aliases. The naive `action.split('_')[0]` rule
   * works for `jito_stake`/`marinade_stake` but breaks for LST aliases that
   * don't match their parent protocol's registry id (e.g. `jupsol_stake`
   * belongs to Jupiter; `bsol_stake` to BlazeStake).
   */
  private static readonly PROTOCOL_ALIASES: Record<string, string> = {
    jupsol: 'jupiter',
    bsol:   'blazestake',
    msol:   'marinade',
  };

  /**
   * Get protocol info for an option (for displaying icons, colors, etc.)
   */
  getProtocolInfo(option: ClarifyOption): ProtocolInfo | undefined {
    const action = option.action;
    if (!action.includes('_')) return undefined;
    const prefix = action.split('_')[0];
    const id = ClarifyCardComponent.PROTOCOL_ALIASES[prefix] ?? prefix;
    return this.protocolRegistry.getProtocol(id);
  }

  /**
   * Check if an option has protocol metadata
   */
  hasProtocolInfo(option: ClarifyOption): boolean {
    return !!this.getProtocolInfo(option);
  }

  select(option: ClarifyOption, index: number): void {
    if (this.selectedIndex !== null) return; // Already selected — wait for parent reset
    const action: ParsedAction = {
      type: option.action,
      params: option.params,
      raw: `[ACTION:${option.action}] ${JSON.stringify(option.params)}`,
    };
    this.optionSelected.emit({ index, action });
  }
}
