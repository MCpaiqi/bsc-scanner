import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import RadarPage from "./pages/RadarPage";
import WalletIntelligencePage from "./pages/WalletIntelligencePage";
import "./styles.css";

function App() {
  return new URLSearchParams(window.location.search).get("view") === "wallet"
    ? <WalletIntelligencePage onBack={() => { window.location.href = "/"; }} />
    : <RadarPage />;
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
