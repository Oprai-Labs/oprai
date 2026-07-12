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
import { TokenRegistryService } from '@core/services/market/token-registry.service';

/** A token's display bits for an option icon (null logo → lettered fallback). */
interface OptionToken { symbol: string; logoURI: string | null; }

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
  private readonly tokenRegistry = inject(TokenRegistryService);

  /** Emit which option the user clicked, plus the parsed action to execute. */
  @Output() optionSelected = new EventEmitter<{ index: number; action: ParsedAction }>();

  readonly categoryIcons: Record<string, string> = {
    stake: 'layers',
    lend: 'banknote',
    borrow: 'credit-card',
    liquidity: 'droplets',
    swap: 'arrow-right-left',
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

  /**
   * Token-pair icons for options that describe a route (e.g. "JupSOL → USDC").
   * Resolves the from/to tokens from the option params first (inputMint /
   * outputMint / token, by address or symbol), then falls back to parsing the
   * "FROM → TO" arrow out of the label. Returns null when neither side
   * resolves, so the template falls back to the generic category glyph.
   */
  optionTokens(option: ClarifyOption): { from: OptionToken | null; to: OptionToken | null } | null {
    const p = option.params ?? {};
    let from = this.resolveToken(p['inputMint'] ?? p['inputToken'] ?? p['fromToken'] ?? p['from']);
    let to = this.resolveToken(p['outputMint'] ?? p['outputToken'] ?? p['toToken'] ?? p['to'] ?? p['token']);
    if (!from || !to) {
      const m = option.label.match(/([A-Za-z0-9$]{2,12})\s*(?:→|->|➜|=>|➔|➙|➛)\s*([A-Za-z0-9$]{2,12})/);
      if (m) {
        from ??= this.resolveToken(m[1]);
        to ??= this.resolveToken(m[2]);
      }
    }
    if (!from && !to) return null;
    return { from, to };
  }

  hasTokenPair(option: ClarifyOption): boolean {
    return !!this.optionTokens(option);
  }

  /**
   * Card-level: are the options distinguished by PROTOCOL or by TOKEN? When
   * every option is the same protocol but a different token (e.g. "USDC vs SOL
   * deposit on Kamino"), repeating the protocol logo on every row is noise —
   * the TOKEN is what the user is choosing, so show token icons instead.
   */
  distinguishBy(): 'protocol' | 'token' {
    const opts = this.clarify.options ?? [];
    const protocols = new Set(
      opts.map(o => (o.action.includes('_') ? o.action.split('_')[0] : o.action)),
    );
    if (protocols.size > 1) return 'protocol';
    const tokens = new Set(
      opts.map(o => this.optionSingleToken(o)?.symbol).filter(Boolean),
    );
    return tokens.size > 1 ? 'token' : 'protocol';
  }

  /** The single distinguishing token for an option (the `to`/deposited token). */
  optionSingleToken(option: ClarifyOption): OptionToken | null {
    const t = this.optionTokens(option);
    return t ? (t.to ?? t.from) : null;
  }

  /** Resolve an address or symbol to display bits; null if unknown/empty. */
  private resolveToken(idOrSymbol?: string): OptionToken | null {
    const raw = (idOrSymbol ?? '').trim().replace(/^\$/, '');
    if (!raw) return null;
    const meta = raw.length >= 32
      ? this.tokenRegistry.getToken(raw)
      : this.tokenRegistry.getBySymbol(raw);
    if (!meta) return null;
    return { symbol: meta.symbol, logoURI: meta.logoURI ?? null };
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
