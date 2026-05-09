import { Pipe, PipeTransform } from '@angular/core';
import type { TransactionType } from '../models/portfolio.models';

const LABELS: Record<TransactionType, string> = {
  'transfer': 'Transfer',
  'swap': 'Swap',
  'stake': 'Stake',
  'unstake': 'Unstake',
  'nft-sale': 'NFT Sale',
  'nft-purchase': 'NFT Purchase',
  'nft-mint': 'NFT Mint',
  'token-mint': 'Token Mint',
  'burn': 'Burn',
  'vote': 'Vote',
  'unknown': 'Unknown',
};

@Pipe({ name: 'transactionTypeLabel', standalone: true })
export class TransactionTypeLabelPipe implements PipeTransform {
  transform(value: TransactionType): string {
    return LABELS[value] ?? value;
  }
}
