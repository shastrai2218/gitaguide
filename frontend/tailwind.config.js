/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        saffron: { 50: '#fff8ed', 100: '#ffeed4', 400: '#f79b3f', 600: '#d9741c' },
        ink: { 700: '#2f3542', 800: '#22262f', 900: '#161920' },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        serif: ['Georgia', 'ui-serif', 'serif'],
      },
    },
  },
  plugins: [],
}
