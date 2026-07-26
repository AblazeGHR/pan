/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./src/**/*.{ts,tsx}', './index.html'],
  theme: {
    extend: {
      colors: {
        'bg-primary': '#0d1117',
        'bg-secondary': '#161b22',
        'bg-tertiary': '#21262d',
        'text-primary': '#e6edf3',
        'text-secondary': '#8b949e',
        'accent': '#58a6ff',
        'accent-hover': '#79c0ff',
        'border-default': '#30363d',
        'success': '#3fb950',
        'warning': '#d29922',
        'danger': '#f85149',
      },
    },
  },
  plugins: [],
};
