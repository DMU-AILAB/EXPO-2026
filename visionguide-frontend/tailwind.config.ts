import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Pretendard', 'Inter', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      colors: {
        brand: { 600: '#2c4be0', 700: '#1d35b5' },
        accent: {
          blue: '#2c4be0',
          emerald: '#15875a',
          amber: '#d98a2b',
          red: '#d3372c',
        },
      },
      animation: {
        'pulse-glow': 'pulseGlow 3s cubic-bezier(0.4,0,0.6,1) infinite',
        'float-slow': 'floatSlow 10s ease-in-out infinite',
        'pulse-dot': 'pulseDot 1.8s infinite',
      },
      keyframes: {
        pulseGlow: {
          '0%,100%': { opacity: '0.45', transform: 'scale(1)' },
          '50%': { opacity: '0.75', transform: 'scale(1.05)' },
        },
        floatSlow: {
          '0%,100%': { transform: 'translate(0,0)' },
          '50%': { transform: 'translate(20px,-15px)' },
        },
        pulseDot: {
          '0%,100%': { opacity: '1', transform: 'scale(1)' },
          '50%': { opacity: '0.4', transform: 'scale(1.25)' },
        },
      },
    },
  },
  plugins: [],
} satisfies Config
