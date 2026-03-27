import { useState, createContext, useContext } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import TopBar from "./components/TopBar";
import GamesPage from "./pages/GamesPage";
import GameDetailPage from "./pages/GameDetailPage";
import LeaderboardPage from "./pages/LeaderboardPage";
import { DataSourceProvider } from "./hooks/useDataSource.jsx";

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
      <DataSourceProvider>
        <BrowserRouter>
          <div
            style={{
              minHeight: "100vh",
              background: "var(--bg-primary)",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <TopBar />
            <main style={{ flex: 1 }}>
              <Routes>
                <Route path="/" element={<GamesPage />} />
                <Route path="/games" element={<GamesPage />} />
                <Route path="/games/:trialId" element={<GameDetailPage />} />
                <Route path="/leaderboard" element={<LeaderboardPage />} />
              </Routes>
            </main>
          </div>
        </BrowserRouter>
      </DataSourceProvider>
    </ThemeContext.Provider>
  );
}

export default App;
