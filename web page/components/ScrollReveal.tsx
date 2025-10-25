'use client';
import { useEffect } from 'react';

export function ScrollReveal() {
  useEffect(() => {
    // Tüm reveal sınıflarını seç
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
    
    // Bidirectional scroll observer - hem yukarı hem aşağı çalışır
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          // Her iki yönde de animasyon tetikle
          if (entry.isIntersecting) {
            (entry.target as HTMLElement).classList.add('visible');
          } else {
            // Yukarı kaydırırken tekrar gizle ki animasyon tekrar çalışsın
            const target = entry.target as HTMLElement;
            const rect = target.getBoundingClientRect();
            
            // Eleman viewport'un üstüne çıktıysa gizle
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












