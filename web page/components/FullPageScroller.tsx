'use client';
import { useEffect, useRef } from 'react';

export function FullPageScroller() {
  const isAnimatingRef = useRef(false);
  const touchStartYRef = useRef<number | null>(null);

  useEffect(() => {
    const getSections = (): HTMLElement[] =>
      Array.from(document.querySelectorAll<HTMLElement>('.snap-viewport > main > section.snap-section, .snap-viewport > footer.snap-section'));

    const getCurrentIndex = (sections: HTMLElement[]): number => {
      const center = window.scrollY + window.innerHeight / 2;
      let bestIdx = 0;
      let bestDist = Infinity;
      sections.forEach((el, idx) => {
        const rect = el.getBoundingClientRect();
        const top = rect.top + window.scrollY;
        const dist = Math.abs(top + rect.height / 2 - center);
        if (dist < bestDist) {
          bestDist = dist;
          bestIdx = idx;
        }
      });
      return bestIdx;
    };

    const gotoIndex = (idx: number) => {
      const sections = getSections();
      if (idx < 0 || idx >= sections.length) return;
      const target = sections[idx];
      isAnimatingRef.current = true;
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      window.setTimeout(() => {
        isAnimatingRef.current = false;
      }, 650);
    };

    const onWheel = (e: WheelEvent) => {
      if (isAnimatingRef.current) return;
      const delta = e.deltaY;
      if (Math.abs(delta) < 20) return;
      const sections = getSections();
      const cur = getCurrentIndex(sections);
      const next = delta > 0 ? Math.min(cur + 1, sections.length - 1) : Math.max(cur - 1, 0);
      if (next !== cur) {
        e.preventDefault();
        gotoIndex(next);
      }
    };

    const onKey = (e: KeyboardEvent) => {
      if (isAnimatingRef.current) return;
      const keys = ['PageDown', 'ArrowDown', ' ', 'Spacebar'];
      const keysUp = ['PageUp', 'ArrowUp'];
      const sections = getSections();
      const cur = getCurrentIndex(sections);
      if (keys.includes(e.key)) {
        e.preventDefault();
        gotoIndex(Math.min(cur + 1, sections.length - 1));
      } else if (keysUp.includes(e.key)) {
        e.preventDefault();
        gotoIndex(Math.max(cur - 1, 0));
      }
    };

    const onTouchStart = (e: TouchEvent) => {
      touchStartYRef.current = e.touches[0]?.clientY ?? null;
    };
    const onTouchEnd = (e: TouchEvent) => {
      if (isAnimatingRef.current) return;
      const start = touchStartYRef.current;
      const end = e.changedTouches[0]?.clientY ?? null;
      if (start == null || end == null) return;
      const delta = start - end;
      if (Math.abs(delta) < 30) return;
      const sections = getSections();
      const cur = getCurrentIndex(sections);
      const next = delta > 0 ? Math.min(cur + 1, sections.length - 1) : Math.max(cur - 1, 0);
      gotoIndex(next);
      touchStartYRef.current = null;
    };

    const container = document.querySelector('.snap-viewport') as HTMLElement | null;
    const targetEl: HTMLElement | Window = container || window;
    targetEl.addEventListener('wheel', onWheel as EventListener, { passive: false } as any);
    targetEl.addEventListener('keydown', onKey as EventListener, { passive: false } as any);
    targetEl.addEventListener('touchstart', onTouchStart as EventListener, { passive: true } as any);
    targetEl.addEventListener('touchend', onTouchEnd as EventListener, { passive: true } as any);
    return () => {
      (targetEl as any).removeEventListener('wheel', onWheel as EventListener);
      (targetEl as any).removeEventListener('keydown', onKey as EventListener);
      (targetEl as any).removeEventListener('touchstart', onTouchStart as EventListener);
      (targetEl as any).removeEventListener('touchend', onTouchEnd as EventListener);
    };
  }, []);

  return null;
}


