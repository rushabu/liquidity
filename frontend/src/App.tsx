import { Navigate, Route, Routes } from "react-router-dom";
import { TopBar } from "./components/TopBar";
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
        <Route path="*" element={<Navigate to="/upload" replace />} />
      </Routes>
    </>
  );
}
