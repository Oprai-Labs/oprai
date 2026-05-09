import { Pipe, PipeTransform } from '@angular/core';
import { marked } from 'marked';
import { DomSanitizer } from '@angular/platform-browser';
import { SecurityContext } from '@angular/core';
import DOMPurify from 'isomorphic-dompurify';

marked.setOptions({
  breaks: true,
  gfm: true,
});

// Register once at module load — avoids hook accumulation across pipe invocations.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('rel', 'noopener noreferrer');
    if (!node.getAttribute('target')) {
      node.setAttribute('target', '_blank');
    }
  }
});

const DOMPURIFY_CONFIG = {
  ALLOWED_TAGS: ['b', 'i', 'em', 'strong', 'code', 'pre', 'br', 'p', 'ul', 'ol', 'li', 'a', 'span', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'img'],
  ALLOWED_ATTR: ['href', 'class', 'target', 'rel', 'src', 'alt', 'width', 'height', 'title', 'role', 'tabindex'],
  // Allow data-* — the protocol-mention shortcode encodes the protocol id in
  // `data-protocol-id` so message-list can dispatch a click into the composer.
  // Safe: data attrs are only read as plain strings by our click handler.
  ALLOW_DATA_ATTR: true,
  ALLOWED_URI_REGEXP: /^(https?:\/\/|\/|assets\/|#)/i,
  FORCE_BODY: false,
};

// Map of inline protocol shortcodes the LLM can emit anywhere in chat text.
// `composerId` is the id used by message-composer's PROTOCOLS list — usually
// the same as the shortcode key, but some (e.g. magiceden → magic_eden) differ.
const PROTOCOL_SHORTCODES: Record<string, { name: string; asset: string; composerId: string }> = {
  jupiter:   { name: 'Jupiter',    asset: 'jupiter.webp',   composerId: 'jupiter' },
  raydium:   { name: 'Raydium',    asset: 'raydium.webp',   composerId: 'raydium' },
  orca:      { name: 'Orca',       asset: 'orca.webp',      composerId: 'orca' },
  meteora:   { name: 'Meteora',    asset: 'meteora.webp',   composerId: 'meteora' },
  kamino:    { name: 'Kamino',     asset: 'kamino.webp',    composerId: 'kamino' },
  marginfi:  { name: 'marginfi',   asset: 'marginfi.webp',  composerId: 'marginfi' },
  solend:    { name: 'Solend',     asset: 'solend.svg',     composerId: 'solend' },
  jito:      { name: 'Jito',       asset: 'jito.webp',      composerId: 'jito' },
  marinade:  { name: 'Marinade',   asset: 'marinade.webp',  composerId: 'marinade' },
  tensor:    { name: 'Tensor',     asset: 'tensor.webp',    composerId: 'tensor' },
  magiceden: { name: 'Magic Eden', asset: 'magiceden.webp', composerId: 'magic_eden' },
  pumpfun:   { name: 'Pump.fun',   asset: 'pumpfun.webp',   composerId: 'pumpfun' },
};

// `:<id>:` optionally followed by the protocol's display name in bold or plain
// — when present, both icon AND name are folded into a single clickable chip.
// The capture for the bold name is non-greedy and stops at the first `**` so
// it can't swallow surrounding emphasis.
const PROTOCOL_SHORTCODE_RE = new RegExp(
  `:(${Object.keys(PROTOCOL_SHORTCODES).join('|')}):` +
  `(?:[ \\t]+(?:\\*\\*([^*\\n]{1,30}?)\\*\\*|([A-Za-z][A-Za-z0-9.]{0,20}(?:\\s[A-Za-z][A-Za-z0-9.]{0,20})?)))?`,
  'gi',
);

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  } as Record<string, string>)[c]!);
}

function expandProtocolShortcodes(input: string): string {
  return input.replace(PROTOCOL_SHORTCODE_RE, (_match, key: string, boldName?: string, plainName?: string) => {
    const meta = PROTOCOL_SHORTCODES[key.toLowerCase()];
    if (!meta) return _match;
    // Prefer the LLM's spelling so we keep casing/accents (e.g. "marginfi" lowercase).
    const display = (boldName ?? plainName ?? meta.name).trim();
    // Encode the composer id BOTH in the class name (always survives sanitizer)
    // and as a data attribute (used when ALLOW_DATA_ATTR is on). Click handler
    // tries class first so the feature works even if data-* is stripped.
    const safeName = escapeHtml(display);
    return (
      `<span class="proto-mention proto-mention--${meta.composerId}" ` +
      `data-protocol-id="${meta.composerId}" role="button" tabindex="0" ` +
      `title="Tag ${safeName} in your next message">` +
      `<img src="assets/icons/protocols/${meta.asset}" alt="${meta.name}" class="proto-icon" width="18" height="18" />` +
      `<span class="proto-mention__name">${safeName}</span>` +
      `</span>`
    );
  });
}

@Pipe({ name: 'markdown', standalone: true })
export class MarkdownPipe implements PipeTransform {
  constructor(private sanitizer: DomSanitizer) {}

  transform(value: string | undefined | null): string {
    if (!value) return '';
    const expanded = expandProtocolShortcodes(value);
    const html = marked.parse(expanded, { async: false }) as string;
    const cleanHtml = DOMPurify.sanitize(html, DOMPURIFY_CONFIG);
    return this.sanitizer.sanitize(SecurityContext.HTML, cleanHtml) ?? '';
  }
}
