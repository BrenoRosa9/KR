import { NavLink, Navigate, Route, Routes } from "react-router";

import { AuthProvider, useAuth } from "./auth";
import { Spinner } from "./components/Ui";
import AnalysisPage from "./pages/AnalysisPage";
import HistoryPage from "./pages/HistoryPage";
import LoginPage from "./pages/LoginPage";
import NewAnalysisPage from "./pages/NewAnalysisPage";

function Shell() {
  const { user, loading, logout } = useAuth();

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner label="Verificando sessão…" />
      </div>
    );
  }

  if (!user) return <LoginPage />;

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `rounded-md px-2.5 py-1.5 text-sm font-medium ${
      isActive
        ? "bg-slate-900 text-white"
        : "text-slate-600 hover:bg-slate-200"
    }`;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-4 border-b border-slate-200 bg-white px-4 py-2">
        <span className="text-sm font-semibold text-slate-900">
          Comparador de documentos
        </span>
        <nav className="flex gap-1">
          <NavLink to="/nova" className={linkClass}>
            Nova análise
          </NavLink>
          <NavLink to="/analises" className={linkClass}>
            Histórico
          </NavLink>
        </nav>
        <div className="ml-auto flex items-center gap-3 text-sm text-slate-500">
          <span>{user.name}</span>
          <button
            onClick={() => void logout()}
            className="text-slate-500 underline-offset-2 hover:text-slate-900 hover:underline"
          >
            Sair
          </button>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-auto">
        <Routes>
          <Route path="/nova" element={<NewAnalysisPage />} />
          <Route path="/analises" element={<HistoryPage />} />
          <Route path="/analises/:id" element={<AnalysisPage />} />
          <Route path="*" element={<Navigate to="/analises" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}
