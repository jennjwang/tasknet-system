/** @type {import('tailwindcss').Config} */
export default {
  // ../validation/ui is bundled into this app (src/main.tsx mounts the validation
  // survey), so its classes must be scanned too or Tailwind never generates them.
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}', '../validation/ui/**/*.{ts,tsx}'],
  theme: {
    extend: {
      animation: {
        fadeSlideIn: 'fadeSlideIn 0.22s ease-out both',
        fadeSlideUp: 'fadeSlideUp 0.45s ease-out both',
        popIn:       'popIn 0.18s ease-out both',
        swipeIn:     'swipeIn 0.26s ease-out both',
        waveBar:     'waveBar 0.9s ease-in-out infinite',
      },
      keyframes: {
        waveBar: {
          '0%, 100%': { transform: 'scaleY(0.4)' },
          '50%':      { transform: 'scaleY(1)' },
        },
        fadeSlideIn: {
          '0%':   { opacity: '0', transform: 'translateY(7px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        // Screen-to-screen swipe inside a validation task card
        // (../validation/ui/components/CoverageTaskCards.tsx): slides in from the right.
        swipeIn: {
          '0%':   { opacity: '0', transform: 'translateX(28px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        fadeSlideUp: {
          '0%':   { opacity: '0', transform: 'translateY(14px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        popIn: {
          '0%':   { opacity: '0', transform: 'scale(0.94)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
      },
    },
  },
  plugins: [],
};
