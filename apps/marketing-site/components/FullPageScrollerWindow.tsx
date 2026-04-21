'use client';
import { useEffect, useRef } from 'react';

export function FullPageScrollerWindow() {
  const isAnimatingRef = useRef(false);
  const touchStartYRef = useRef<number | null>(null);
  const activeIndexRef = useRef(0);
  const sectionsRef = useRef<HTMLElement[]>([]);

  useEffect(() => {
    const collectSections = () => Array.from(document.querySelectorAll<HTMLElement>('section.snap-section'));
    const applyActive = (list: HTMLElement[], idx: number) => {
      list.forEach((el, i) => el.classList.toggle('is-active', i === idx));
      activeIndexRef.current = idx;
    };

    const computeIndexFromScroll = (list: HTMLElement[]) => {
      const midpoint = window.scrollY + window.innerHeight / 2;
      let winner = 0;
      let best = Infinity;
      list.forEach((el, i) => {
        const top = el.offsetTop;
        const center = top + el.offsetHeight / 2;
        const dist = Math.abs(center - midpoint);
        if (dist < best) {
          best = dist;
          winner = i;
        }
      });
      return winner;
    };

    const handleScroll = () => {
      if (isAnimatingRef.current) return;
      const list = sectionsRef.current;
      if (!list.length) return;
      const idx = computeIndexFromScroll(list);
      if (idx !== activeIndexRef.current) {
        applyActive(list, idx);
      }
    };

    const rebuildSections = () => {
      sectionsRef.current = collectSections();
      if (!sectionsRef.current.length) return;
      const idx = computeIndexFromScroll(sectionsRef.current);
      applyActive(sectionsRef.current, idx);
    };

    const isOverflowing = (el: HTMLElement | undefined | null) => {
      if (!el) return false;
      return el.scrollHeight > window.innerHeight + 32;
    };

    const goTo = (idx: number) => {
      const list = sectionsRef.current;
      if (!list.length) return;
      const currentIndex = activeIndexRef.current;
      const clamped = Math.max(0, Math.min(idx, list.length - 1));
      if (clamped === currentIndex) return;
      const target = list[clamped];
      if (!target) return;
      isAnimatingRef.current = true;
      applyActive(list, clamped);
      window.scrollTo({ top: target.offsetTop, behavior: 'smooth' });
      window.setTimeout(() => {
        isAnimatingRef.current = false;
        handleScroll();
      }, 700);
    };

    const currentSection = () => sectionsRef.current[activeIndexRef.current];

    const atBoundary = (el: HTMLElement, delta: number) => {
      const sectionTop = el.offsetTop;
      const sectionBottom = sectionTop + el.offsetHeight - window.innerHeight;
      const pos = window.scrollY;
      const tolerance = 8;
      if (delta > 0) {
        return pos >= sectionBottom - tolerance;
      }
      if (delta < 0) {
        return pos <= sectionTop + tolerance;
      }
      return false;
    };

    const onWheel = (e: WheelEvent) => {
      if (isAnimatingRef.current) return;
      const list = sectionsRef.current;
      if (!list.length) return;
      const current = currentSection();
      if (!current) return;
      if (Math.abs(e.deltaY) < 10) return;
      const direction = e.deltaY > 0 ? 1 : -1;
      const atFirst = activeIndexRef.current <= 0;
      const atLast = activeIndexRef.current >= list.length - 1;
      if ((direction > 0 && atLast) || (direction < 0 && atFirst)) {
        return;
      }
      if (isOverflowing(current) && !atBoundary(current, e.deltaY)) {
        return;
      }
      e.preventDefault();
      goTo(activeIndexRef.current + direction);
    };

    const onKey = (e: KeyboardEvent) => {
      if (isAnimatingRef.current) return;
      const list = sectionsRef.current;
      if (!list.length) return;
      const current = currentSection();
      if (!current) return;
      const downKeys = ['PageDown', 'ArrowDown', ' ', 'Spacebar'];
      const upKeys = ['PageUp', 'ArrowUp'];
      if (downKeys.includes(e.key)) {
        const atLast = activeIndexRef.current >= list.length - 1;
        if (atLast) return;
        if (isOverflowing(current) && !atBoundary(current, 1)) return;
        e.preventDefault();
        goTo(activeIndexRef.current + 1);
      } else if (upKeys.includes(e.key)) {
        const atFirst = activeIndexRef.current <= 0;
        if (atFirst) return;
        if (isOverflowing(current) && !atBoundary(current, -1)) return;
        e.preventDefault();
        goTo(activeIndexRef.current - 1);
      }
    };

    const onTouchStart = (e: TouchEvent) => {
      touchStartYRef.current = e.touches[0]?.clientY ?? null;
    };

    const onTouchEnd = (e: TouchEvent) => {
      if (isAnimatingRef.current) return;
      const list = sectionsRef.current;
      if (!list.length) return;
      const current = currentSection();
      if (!current) return;
      const start = touchStartYRef.current;
      const end = e.changedTouches[0]?.clientY ?? null;
      if (start == null || end == null) return;
      const delta = start - end;
      if (Math.abs(delta) < 45) return;
      const direction = delta > 0 ? 1 : -1;
      const atFirst = activeIndexRef.current <= 0;
      const atLast = activeIndexRef.current >= list.length - 1;
      if ((direction > 0 && atLast) || (direction < 0 && atFirst)) {
        return;
      }
      if (isOverflowing(current) && !atBoundary(current, delta)) return;
      goTo(activeIndexRef.current + direction);
    };

    window.addEventListener('wheel', onWheel, { passive: false });
    window.addEventListener('keydown', onKey, { passive: false });
    window.addEventListener('touchstart', onTouchStart, { passive: true });
    window.addEventListener('touchend', onTouchEnd, { passive: true });
    window.addEventListener('scroll', handleScroll, { passive: true });
    window.addEventListener('resize', rebuildSections);

    rebuildSections();
    window.requestAnimationFrame(handleScroll);

    return () => {
      window.removeEventListener('wheel', onWheel as EventListener);
      window.removeEventListener('keydown', onKey as EventListener);
      window.removeEventListener('touchstart', onTouchStart as EventListener);
      window.removeEventListener('touchend', onTouchEnd as EventListener);
      window.removeEventListener('scroll', handleScroll as EventListener);
      window.removeEventListener('resize', rebuildSections as EventListener);
    };
  }, []);

  return null;
}
