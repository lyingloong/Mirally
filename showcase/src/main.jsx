import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./style.css";
import "./chapters.css";
import "./capability-visuals.css";
import "./next-capabilities.css";
import "./hero-experience.css";
import "./business-visual.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
