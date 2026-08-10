import {
  Component,
  Input,
  Output,
  EventEmitter,
  ChangeDetectionStrategy,
  OnInit,
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
export class ClarifyCardComponent implements OnInit {
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

  /**
   * On a fresh page load the card can re-hydrate from stored chat history BEFORE
   * the token list is fetched — `resolveToken` then returns null and every option
   * falls back to the generic protocol mark. Kick the load here; the reactive
   * `version` signal (read in `resolveToken`) re-renders the icons once it lands.
   */
  ngOnInit(): void {
    void this.tokenRegistry.ensureLoaded();
  }

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
    // A validator is not a protocol. Matching on the action prefix put the
    // Drift protocol's logo next to a validator that happens to be called
    // Drift, and the Solana Foundation's next to nothing at all.
    if (action.startsWith('native_stake')) return undefined;
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
    // `tokenA`/`tokenB` (+ mintA/mintB) are how LP / pool-pair options name
    // their two sides — a Raydium/Orca/Meteora "SOL/USDC" pool-pick carries
    // these, not inputMint/outputMint. Without them the pair fell back to the
    // generic protocol logo instead of the two token coins.
    let from = this.resolveToken(
      p['inputMint'] ?? p['inputToken'] ?? p['fromToken'] ?? p['from'] ?? p['tokenA'] ?? p['mintA'],
    );
    // `reserve` is how Kamino lend/borrow options name the subject token.
    let to = this.resolveToken(
      p['outputMint'] ?? p['outputToken'] ?? p['toToken'] ?? p['to'] ?? p['token'] ?? p['reserve'] ?? p['tokenB'] ?? p['mintB'],
    );
    if (!from || !to) {
      // Separator-agnostic label parse: pull every symbol-shaped word out of
      // the label and keep the first two that resolve to a real token. This
      // handles "SOL/USDC", "SOL - USDC", "SOL → USDC" AND unicode separator
      // variants (／ ⁄ ∕) the LLM sometimes emits — where a fixed separator
      // regex silently failed and the option fell back to the protocol logo.
      const candidates = option.label.match(/[A-Za-z][A-Za-z0-9$]{1,11}/g) ?? [];
      const resolved: OptionToken[] = [];
      for (const c of candidates) {
        const t = this.resolveToken(c);
        if (t) resolved.push(t);
        if (resolved.length === 2) break;
      }
      from ??= resolved[0] ?? null;
      to ??= resolved[1] ?? null;
    }
    if (!from && !to) return null;
    return { from, to };
  }

  hasTokenPair(option: ClarifyOption): boolean {
    return !!this.optionTokens(option);
  }

  /** Both sides of a pool/pair option resolved — render the two coins side by
   *  side (SOL + USDC) rather than a single token or the protocol logo. */
  optionFullPair(option: ClarifyOption): { from: OptionToken; to: OptionToken } | null {
    const t = this.optionTokens(option);
    return t && t.from && t.to ? { from: t.from, to: t.to } : null;
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
    // Different tokens → clearly token-distinguished. Same single token across
    // every option (e.g. "10 / 50 / 100 USDT borrow on Kamino") → the amount is
    // what differs, but the meaningful icon is still that token, not the
    // protocol logo already shown in the header. Only fall back to the protocol
    // glyph when no option resolves a token at all (e.g. validator lists).
    if (tokens.size > 1) return 'token';
    if (tokens.size === 1 && opts.every(o => this.optionSingleToken(o))) return 'token';
    return 'protocol';
  }

  /** The single distinguishing token for an option (the `to`/deposited token). */
  optionSingleToken(option: ClarifyOption): OptionToken | null {
    const t = this.optionTokens(option);
    return t ? (t.to ?? t.from) : null;
  }

  /**
   * The one protocol shared by EVERY option — used to brand the card header
   * with the real protocol logo (e.g. Kamino) instead of a generic glyph.
   * Null when options span multiple protocols (then the per-row logos carry it).
   */
  /** SOL's own mark, for a card whose options are all native staking — there
   *  is no protocol logo to show, because the protocol is Solana itself. */
  cardTokenIcon(): string | null {
    const opts = this.clarify.options ?? [];
    if (!opts.length || !opts.every(o => o.action.startsWith('native_stake'))) return null;
    return this.resolveTokenPublic('So11111111111111111111111111111111111111112')?.logoURI ?? null;
  }

  /** The private resolver, reachable from the template-facing helpers above. */
  private resolveTokenPublic(id: string): OptionToken | null {
    return this.resolveToken(id);
  }

  cardProtocol(): ProtocolInfo | null {
    const opts = this.clarify.options ?? [];
    if (!opts.length) return null;
    const infos = opts.map(o => this.getProtocolInfo(o));
    const first = infos[0];
    if (!first) return null;
    return infos.every(i => i?.id === first.id) ? first : null;
  }

  /** Resolve an address or symbol to display bits; null if unknown/empty. */
  private resolveToken(idOrSymbol?: string): OptionToken | null {
    // Touch the registry's version signal so this (template-invoked) lookup is
    // reactive: when the token list finishes loading after a reload, the bump
    // re-runs OnPush change detection and the real icons replace the fallback.
    this.tokenRegistry.version();
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
