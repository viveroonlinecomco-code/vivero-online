import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        primary: '#325926',
        'bg-light': '#f7f7f6',
        'bg-dark': '#171d15',
      },
      fontFamily: { display: ['Inter', 'sans-serif'] },
    },
  },
  plugins: [],
}
export default config
