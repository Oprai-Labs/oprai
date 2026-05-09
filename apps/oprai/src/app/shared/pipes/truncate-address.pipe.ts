import { Pipe, PipeTransform } from '@angular/core';

/**
 * Truncates a Solana address for display: "HwM3CnFN...k9f2"
 * Usage: {{ address | truncateAddress }}
 *        {{ address | truncateAddress:6:6 }}
 */
@Pipe({
  name: 'truncateAddress',
  standalone: true,
})
export class TruncateAddressPipe implements PipeTransform {
  transform(
    value: string | null | undefined,
    startChars: number = 4,
    endChars: number = 4
  ): string {
    if (!value) return '';
    if (value.length <= startChars + endChars + 3) return value;
    return `${value.slice(0, startChars)}...${value.slice(-endChars)}`;
  }
}
