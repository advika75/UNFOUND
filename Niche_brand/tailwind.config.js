/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#f4efe4",
        ink: "#1f1d1a",
        accent: "#ca6d38",
        pine: "#284b3f",
      },
      boxShadow: {
        card: "0 18px 40px rgba(38, 28, 15, 0.12)",
      },
    },
  },
  plugins: [],
};
