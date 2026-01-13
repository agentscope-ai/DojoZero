import { useState, createContext, useContext } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Lobby from "./components/Lobby";
import GameRoom from "./components/GameRoom";

// Theme context
export const ThemeContext = createContext();

export function useTheme() {
  return useContext(ThemeContext);
}

function App() {
  const [theme, setTheme] = useState("dark");

  const toggleTheme = () => {
    const newTheme = theme === "dark" ? "light" : "dark";
    setTheme(newTheme);
    document.documentElement.setAttribute("data-theme", newTheme);
  };

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      <BrowserRouter>
        <div
          className="app-container"
          style={{ minHeight: "100vh", background: "var(--bg-primary)", overflowX: "hidden", overflowY: "auto" }}
        >
          <Routes>
            <Route path="/" element={<Lobby />} />
            <Route path="/room/:trialId" element={<GameRoom />} />
          </Routes>
        </div>
      </BrowserRouter>
    </ThemeContext.Provider>
  );
}

export default App;

