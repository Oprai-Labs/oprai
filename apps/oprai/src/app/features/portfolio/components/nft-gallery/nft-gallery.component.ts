import { Component, Input, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import type { NftAsset, NftCollection, LoadingState } from '../../models/portfolio.models';

/**
 * Heuristic spam-NFT detector. Helius DAS doesn't expose an explicit
 * verified/spam flag (Magic Eden's verified_collections list would be
 * authoritative but is rate-limited per-IP), so we approximate using:
 *   - cNFT with no collection metadata → ~always spam airdrop
 *   - obvious phishing keywords in the name (URLs, claim/airdrop tags)
 *   - missing image AND missing collection
 * Scoring is intentionally conservative — false positives are worse than
 * false negatives here, since users can always toggle the filter off.
 */
const SPAM_NAME_PATTERNS = [
  /\b(claim|airdrop|reward|free|gift|winner|bonus|visit|access|whitelist)\b/i,
  /https?:\/\//i,
  /\.(com|io|xyz|net|org|app|fi|finance|claim|gift)\b/i,
  /[$€£]\s*\d/,                  // "$1000 SOL"
  /\b(eligible|\d+\s*sol\s*free)/i,
];

function isLikelyScam(nft: NftAsset): boolean {
  const name = (nft.name || '').toLowerCase();
  for (const re of SPAM_NAME_PATTERNS) if (re.test(name)) return true;
  // No image AND no collection → almost certainly junk airdrop
  if (!nft.imageUri && !nft.collectionId) return true;
  // Compressed cNFT with no collection identity is the standard spam shape
  if (nft.compressed && !nft.collectionName) return true;
  return false;
}

@Component({
  selector: 'app-nft-gallery',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  templateUrl: './nft-gallery.component.html',
  styleUrl: './nft-gallery.component.scss',
})
export class NftGalleryComponent {
  @Input() set nfts(value: NftAsset[]) { this._nfts.set(value); }
  @Input() set collections(value: NftCollection[]) { this._collections.set(value); }
  @Input() loadingState: LoadingState = 'idle';

  private readonly _nfts = signal<NftAsset[]>([]);
  private readonly _collections = signal<NftCollection[]>([]);

  readonly viewMode = signal<'all' | 'collection'>('all');
  readonly hideSpam = signal(true);

  /** NFTs with the spam filter applied (default on). */
  readonly visibleNfts = computed(() => {
    const all = this._nfts();
    if (!this.hideSpam()) return all;
    return all.filter(n => !isLikelyScam(n));
  });

  /** Collections with spam items filtered + empty-after-filter collections dropped. */
  readonly visibleCollections = computed(() => {
    const cols = this._collections();
    if (!this.hideSpam()) return cols;
    return cols
      .map(c => ({ ...c, items: c.items.filter(i => !isLikelyScam(i)) }))
      .filter(c => c.items.length > 0);
  });

  readonly spamCount = computed(() => this._nfts().filter(isLikelyScam).length);

  toggleView(): void {
    this.viewMode.update((v) => (v === 'all' ? 'collection' : 'all'));
  }

  toggleHideSpam(): void {
    this.hideSpam.update(v => !v);
  }

  onImageError(event: Event): void {
    const img = event.target as HTMLImageElement;
    img.style.display = 'none';
    const fallback = img.nextElementSibling;
    if (fallback) (fallback as HTMLElement).style.display = 'flex';
  }
}
