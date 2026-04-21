import { Pipe, PipeTransform } from '@angular/core';

/**
 * Converts a date string or Date to a relative time string: "2 hours ago", "just now"
 * Usage: {{ dateString | timeAgo }}
 */
@Pipe({
  name: 'timeAgo',
  standalone: true,
})
export class TimeAgoPipe implements PipeTransform {
  transform(value: string | Date | null | undefined): string {
    if (!value) return '';

    const date = typeof value === 'string' ? new Date(value) : value;
    const now = new Date();
    const seconds = Math.floor((now.getTime() - date.getTime()) / 1000);

    if (seconds < 0) return 'just now';

    const intervals: [number, string][] = [
      [31536000, 'year'],
      [2592000, 'month'],
      [604800, 'week'],
      [86400, 'day'],
      [3600, 'hour'],
      [60, 'minute'],
    ];

    for (const [secondsInUnit, unitName] of intervals) {
      const count = Math.floor(seconds / secondsInUnit);
      if (count >= 1) {
        return count === 1
          ? `1 ${unitName} ago`
          : `${count} ${unitName}s ago`;
      }
    }

    if (seconds < 10) return 'just now';
    return `${seconds} seconds ago`;
  }
}
