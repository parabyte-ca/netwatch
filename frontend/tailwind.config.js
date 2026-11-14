/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      keyframes: {
        // Moves the diagonal stripes of the update progress bar.
        stripes: { '0%': { backgroundPosition: '0 0' }, '100%': { backgroundPosition: '28px 0' } },
        // Draws the success checkmark (stroke-dasharray set on the path).
        draw: { '0%': { strokeDashoffset: '48' }, '100%': { strokeDashoffset: '0' } },
        // A soft light sweeping along the slim top bar.
        sweep: { '0%': { transform: 'translateX(-100%)' }, '100%': { transform: 'translateX(400%)' } },
      },
      animation: {
        stripes: 'stripes 0.8s linear infinite',
        draw: 'draw 0.6s ease-out 0.15s both',
        sweep: 'sweep 1.6s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};
