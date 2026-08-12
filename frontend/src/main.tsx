import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { pdfjs } from "react-pdf";

import App from "./App";
import "./index.css";

// O worker do pdf.js é resolvido pelo bundler a partir do pacote instalado, e
// não de uma CDN: a aplicação precisa funcionar numa rede interna sem saída
// para a internet.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
