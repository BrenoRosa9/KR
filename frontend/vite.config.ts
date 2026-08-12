import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Em desenvolvimento o Vite roda em outra porta; o proxy mantém a mesma
    // origem para o navegador, o que faz o cookie de sessão HttpOnly funcionar
    // sem precisar afrouxar CORS nem SameSite.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        // O pdf.js é grande e raramente muda: separá-lo mantém o cache do
        // navegador útil entre atualizações da aplicação.
        manualChunks: (id) =>
          id.includes("pdfjs-dist") || id.includes("react-pdf")
            ? "pdf"
            : undefined,
      },
    },
  },
});
