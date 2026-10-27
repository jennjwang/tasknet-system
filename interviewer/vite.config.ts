import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from 'tailwindcss';
import autoprefixer from 'autoprefixer';

export default defineConfig({
  plugins: [react()],
  // Tailwind (src/index.css's @tailwind directives, configured in tailwind.config.js), then vendor prefixes.
  css: { postcss: { plugins: [tailwindcss(), autoprefixer()] } },
  // ../validation/ui sits outside this package; resolve its `react` imports from this node_modules
  resolve: { dedupe: ['react', 'react-dom'] },
  server: {
    fs: { allow: ['..'] },   // ../validation/ui is imported by src/main.tsx
    proxy: {
      '/api': `http://localhost:${process.env.API_PORT ?? 3001}`,
    },
  },
});
