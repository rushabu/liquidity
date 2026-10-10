import { Navigate, Route, Routes } from "react-router-dom";
import { TopBar } from "./components/TopBar";
import { AiGapPage } from "./pages/AiGapPage";
import { WhatIfPage } from "./pages/WhatIfPage";
import { LadderPage } from "./pages/LadderPage";
import { UploadPage } from "./pages/UploadPage";

export default function App() {
  return (
    <>
      <TopBar />
      <Routes>
        <Route path="/" element={<Navigate to="/upload" replace />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/ladder" element={<LadderPage />} />
        <Route path="/ai" element={<AiGapPage />} />
        <Route path="/whatif" element={<WhatIfPage />} />
        <Route path="/ai-ladder" element={<Navigate to="/ai?view=ladder" replace />} />
        <Route path="/compare" element={<Navigate to="/ai" replace />} />
        <Route path="*" element={<Navigate to="/upload" replace />} />
      </Routes>
    </>
  );
}
