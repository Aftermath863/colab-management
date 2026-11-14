/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          '"SF Pro Text"',
          '"SF Pro Display"',
          '"Inter"',
          '"Segoe UI"',
          'Roboto',
          'Helvetica',
          'Arial',
          'sans-serif',
        ],
        mono: [
          '"SF Mono"',
          'ui-monospace',
          'SFMono-Regular',
          'Menlo',
          'Monaco',
          'Consolas',
          'monospace',
        ],
      },
      colors: {
        slate: {
          750: '#243045',
          850: '#162033',
          925: '#0b1220',
          975: '#050811',
        },
        obsidian: {
          950: '#070b14',
          900: '#0c1222',
          850: '#11192e',
        },
        colab: {
          obsidian: '#070b14',
          dark: '#0f172a',
          card: '#1e293b',
          border: '#334155',
          accent: '#3b82f6',
          indigo: '#6366f1',
          cyan: '#06b6d4',
          amber: '#f59e0b',
        },
      },
      boxShadow: {
        glass: '0 12px 40px -12px rgba(2, 6, 23, 0.72), inset 0 1px 0 rgba(255, 255, 255, 0.08)',
        'glass-lg': '0 24px 64px -16px rgba(2, 6, 23, 0.85), inset 0 1px 0 rgba(255, 255, 255, 0.1)',
        'glow-blue': '0 8px 24px -6px rgba(59, 130, 246, 0.42), inset 0 1px 0 rgba(255, 255, 255, 0.2)',
        'glow-indigo': '0 8px 24px -6px rgba(99, 102, 241, 0.42), inset 0 1px 0 rgba(255, 255, 255, 0.2)',
      },
      keyframes: {
        macPulseSlow: {
          '0%, 100%': { opacity: '0.75' },
          '50%': { opacity: '1' },
        },
      },
      animation: {
        'mac-pulse-slow': 'macPulseSlow 3.5s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};
