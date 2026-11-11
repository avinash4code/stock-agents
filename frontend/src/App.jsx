import { NavLink, Route, Routes } from "react-router-dom";
import Home from "./pages/Home.jsx";
import Stock from "./pages/Stock.jsx";
import History from "./pages/History.jsx";
import RunPage from "./pages/RunPage.jsx";
import ReportPage from "./pages/ReportPage.jsx";

export default function App() {
  return (
    <>
      <header className="topbar">
        <NavLink to="/" className="brand">Stock Analyst</NavLink>
        <nav>
          <NavLink to="/" end>Analyse</NavLink>
          <NavLink to="/history">History</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/stock/:symbol" element={<Stock />} />
          <Route path="/run/:id" element={<RunPage />} />
          <Route path="/history" element={<History />} />
          <Route path="/report/:id" element={<ReportPage />} />
          <Route path="*" element={<p className="muted">Page not found.</p>} />
        </Routes>
      </main>
    </>
  );
}
