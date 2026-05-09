import { Injectable } from '@angular/core';
import { Observable, from } from 'rxjs';
import { environment } from '../../../environments/environment';

/** Returned from POST /upload/image */
export interface UploadResult {
  url: string;       // Public HTTP URL (https://api.oprai.xyz/uploads/img/...)
  filename: string;  // Generated filename (uuid.ext)
  ipfsHash: string;  // Always empty string — kept for backward compat
}

/** Returned from POST /upload/metadata */
export interface MetadataUploadResult {
  url: string;       // Public HTTP URL of the metadata JSON
  filename: string;  // e.g. abc123.json
}

/** Token metadata payload for POST /upload/metadata */
export interface TokenMetadataPayload {
  name: string;
  symbol: string;
  description: string;
  image: string;        // public HTTP URL of the token image
  twitter?: string;
  telegram?: string;
  website?: string;
  banner?: string;
  showName?: boolean;
}

// pump.fun IPFS upload response
interface PumpIpfsResponse {
  metadataUri?: string;  // full ipfs URI for metadata
  image?: string;        // IPFS URL for the image
}

@Injectable({ providedIn: 'root' })
export class UploadService {
  private readonly baseUrl = environment.apiBase;

  /**
   * Upload an image file to our local storage.
   * Returns a public HTTP URL for the uploaded image.
   */
  uploadImage(file: File): Observable<UploadResult> {
    const formData = new FormData();
    formData.append('file', file);
    return from(this.postWithAuth<UploadResult>('/upload/image', formData));
  }

  /**
   * Upload token metadata JSON to our local storage.
   * Returns a public HTTP URL for the metadata JSON.
   * This URL is used as the on-chain token URI in the pump.fun Create instruction.
   */
  uploadMetadata(payload: TokenMetadataPayload): Observable<MetadataUploadResult> {
    return from(this.postWithAuth<MetadataUploadResult>('/upload/metadata', payload));
  }

  /**
   * Upload token image + metadata directly to pump.fun's IPFS.
   * Returns a public metadataUri usable as the on-chain token URI.
   * This guarantees pump.fun's indexer can always reach the metadata.
   */
  async uploadToPumpFunIpfs(
    imageFile: File,
    metadata: Omit<TokenMetadataPayload, 'image'>,
  ): Promise<string> {
    const formData = new FormData();
    formData.append('file', imageFile, imageFile.name);
    formData.append('name', metadata.name);
    formData.append('symbol', metadata.symbol);
    formData.append('description', metadata.description ?? '');
    if (metadata.twitter)  formData.append('twitter',  metadata.twitter);
    if (metadata.telegram) formData.append('telegram', metadata.telegram);
    if (metadata.website)  formData.append('website',  metadata.website);
    formData.append('showName', 'true');
    formData.append('createdOn', 'https://pump.fun');

    const res = await fetch('https://pump.fun/api/ipfs', {
      method: 'POST',
      body: formData,
    });

    if (!res.ok) {
      const txt = await res.text().catch(() => '');
      throw new Error(`pump.fun IPFS upload failed (${res.status}): ${txt}`);
    }

    const data: PumpIpfsResponse = await res.json();
    const uri = data.metadataUri;
    if (!uri) throw new Error('pump.fun IPFS upload returned no metadataUri');
    return uri;
  }

  /**
   * Returns the URL as-is. No IPFS → HTTP conversion needed since we use
   * self-hosted storage. Kept for backward compatibility.
   */
  getGatewayUrl(url: string): string {
    if (!url) return '';
    // Legacy IPFS URLs from before migration — convert to our gateway if encountered
    if (url.startsWith('ipfs://')) {
      // Attempt to resolve via public IPFS gateways as a last resort
      return `https://ipfs.io/ipfs/${url.replace('ipfs://', '')}`;
    }
    return url;
  }

  // ─── Private helpers ───────────────────────────────────────────────────────

  private async postWithAuth<T>(path: string, body: FormData | object): Promise<T> {
    const token = localStorage.getItem('oprai-auth-token') ?? '';
    const headers: Record<string, string> = {
      Authorization: `Bearer ${token}`,
      'X-Requested-With': 'XMLHttpRequest',
    };
    let bodyInit: BodyInit;

    if (body instanceof FormData) {
      bodyInit = body;
      // Let browser set multipart Content-Type with boundary
    } else {
      headers['Content-Type'] = 'application/json';
      bodyInit = JSON.stringify(body);
    }

    const response = await fetch(`${this.baseUrl}${path}`, {
      method: 'POST',
      headers,
      body: bodyInit,
    });

    if (!response.ok) {
      const errorText = await response.text().catch(() => '');
      throw new Error(errorText || `Upload failed with status ${response.status}`);
    }

    return response.json() as Promise<T>;
  }
}
