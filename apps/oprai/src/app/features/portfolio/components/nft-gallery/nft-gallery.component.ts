import { Component, Input, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { LucideAngularModule } from 'lucide-angular';
import type { NftAsset, NftCollection, LoadingState } from '../../models/portfolio.models';

@Component({
  selector: 'app-nft-gallery',
  standalone: true,
  imports: [CommonModule, LucideAngularModule],
  templateUrl: './nft-gallery.component.html',
  styleUrl: './nft-gallery.component.scss',
})
export class NftGalleryComponent {
  @Input() nfts: NftAsset[] = [];
  @Input() collections: NftCollection[] = [];
  @Input() loadingState: LoadingState = 'idle';

  readonly viewMode = signal<'all' | 'collection'>('all');

  toggleView(): void {
    this.viewMode.update((v) => (v === 'all' ? 'collection' : 'all'));
  }

  onImageError(event: Event): void {
    const img = event.target as HTMLImageElement;
    img.style.display = 'none';
    const fallback = img.nextElementSibling;
    if (fallback) (fallback as HTMLElement).style.display = 'flex';
  }
}
