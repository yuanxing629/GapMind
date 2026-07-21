import { Routes, Route } from "react-router-dom";
import AppLayout from "./components/AppLayout";
import DashboardPage from "./pages/DashboardPage";
import WorkspacesPage from "./pages/WorkspacesPage";
import WorkspaceDetailPage from "./pages/WorkspaceDetailPage";
import NotFoundPage from "./pages/NotFoundPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/workspaces" element={<WorkspacesPage />} />
        <Route path="/workspaces/:id" element={<WorkspaceDetailPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
