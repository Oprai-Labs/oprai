'use client';
import { useEffect } from 'react';

export function ScrollReveal() {
  useEffect(() => {
    // Select all reveal selectors
    const selectors = [
      '.reveal',
      '.reveal-heading',
      '.reveal-text',
      '.reveal-eyebrow',
      '.reveal-cascade'
    ];
    
    const elements = Array.from(
      document.querySelectorAll(selectors.join(', '))
    ) as HTMLElement[];
    
    // Bidirectional scroll observer — triggers in both scroll directions
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          // Trigger animation when entering viewport
          if (entry.isIntersecting) {
            (entry.target as HTMLElement).classList.add('visible');
          } else {
            // Hide when scrolled past so animation replays on re-entry
            const target = entry.target as HTMLElement;
            const rect = target.getBoundingClientRect();
            
            // Hide if element scrolled above the viewport
            if (rect.bottom < 0) {
              target.classList.remove('visible');
            }
          }
        });
      },
      { 
        rootMargin: '50px 0px -50px 0px', 
        threshold: [0, 0.1, 0.5, 1]
      }
    );
    
    elements.forEach((el) => observer.observe(el));
    
    return () => observer.disconnect();
  }, []);
  
  return null;
}












