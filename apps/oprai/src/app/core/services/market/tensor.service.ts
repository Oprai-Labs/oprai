import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiService } from '@core/services/api.service';

export const TENSOR_API = 'https://api.tensor.so/v1';

@Injectable({ providedIn: 'root' })
export class TensorService {
  private readonly api = inject(ApiService);

  // ── Data queries ──────────────────────────────────────────────────────────

  getCollectionInfo(collectionSlug: string): Observable<any> {
    return this.api.post('/actions/build', {
      type: 'tensor_collection_info',
      params: { collectionSlug },
    });
  }

  getNFTInfo(mintAddress: string): Observable<any> {
    return this.api.post('/actions/build', {
      type: 'tensor_nft_info',
      params: { mintAddress },
    });
  }

  getWalletNFTs(wallet: string): Observable<any> {
    return this.api.post('/actions/build', {
      type: 'tensor_wallet_nfts',
      params: { wallet },
    });
  }

  getListings(collectionSlug: string, limit = '20'): Observable<any> {
    return this.api.post('/actions/build', {
      type: 'tensor_listings',
      params: { collectionSlug, limit },
    });
  }

  // ── Transaction builders ──────────────────────────────────────────────────

  buildBuy(mintAddress: string, maxPrice: string): Observable<any> {
    return this.api.post('/actions/build', {
      type: 'tensor_buy',
      params: { mintAddress, maxPrice },
    });
  }

  buildList(mintAddress: string, price: string): Observable<any> {
    return this.api.post('/actions/build', {
      type: 'tensor_list',
      params: { mintAddress, price },
    });
  }

  buildCancelListing(mintAddress: string): Observable<any> {
    return this.api.post('/actions/build', {
      type: 'tensor_cancel_listing',
      params: { mintAddress },
    });
  }

  buildMakeOffer(collectionSlug: string, price: string, quantity = '1'): Observable<any> {
    return this.api.post('/actions/build', {
      type: 'tensor_make_offer',
      params: { collectionSlug, price, quantity },
    });
  }

  buildCancelOffer(bidId: string): Observable<any> {
    return this.api.post('/actions/build', {
      type: 'tensor_cancel_offer',
      params: { bidId },
    });
  }
}
