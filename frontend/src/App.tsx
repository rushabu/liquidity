import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { AuthProvider, RequireAuth } from "./auth/AuthContext";
import { TopBar } from "./components/TopBar";
import { AiGapPage } from "./pages/AiGapPage";
import { LadderPage } from "./pages/LadderPage";
import { LoginPage } from "./pages/LoginPage";
import { UploadPage } from "./pages/UploadPage";
import { WhatIfPage } from "./pages/WhatIfPage";

/** Every page except /login needs a signed-in user; data is scoped to the user's role/location by the API. */
function Shell() {
  return (
    <RequireAuth>
      <TopBar />
      <Outlet />
    </RequireAuth>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<Shell />}>
          <Route path="/" element={<Navigate to="/upload" replace />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/ladder" element={<LadderPage />} />
          <Route path="/ai" element={<AiGapPage />} />
          <Route path="/whatif" element={<WhatIfPage />} />
          <Route path="/ai-ladder" element={<Navigate to="/ai?view=ladder" replace />} />
          <Route path="/compare" element={<Navigate to="/ai" replace />} />
          <Route path="*" element={<Navigate to="/upload" replace />} />
        </Route>
      </Routes>
    </AuthProvider>
  );
}
