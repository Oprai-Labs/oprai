/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}'
  ],
  theme: {
    extend: {
      colors: {
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))'
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))'
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))'
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))'
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))'
        },
        border: 'hsl(var(--border))'
      },
      container: {
        center: true,
        padding: {
          DEFAULT: '1rem',
          lg: '2rem'
        },
        screens: {
          '2xl': '1280px'
        }
      },
      backgroundImage: {
        'radial-brand': 'radial-gradient(1200px 600px at 50% -10%, rgba(56,189,248,0.12), rgba(147,51,234,0.08), transparent)',
        'brand-gradient': 'linear-gradient(90deg, #14F1C2 0%, #6A5CFF 50%, #A855F7 100%)'
      }
    }
  },
  plugins: []
};











