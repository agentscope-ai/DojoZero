import { useState, createContext, useContext } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import TopBar from "./components/TopBar";
import GamesPage from "./pages/GamesPage";
import LeaderboardPage from "./pages/LeaderboardPage";
import BlogPage from "./pages/BlogPage";
import AboutPage from "./pages/AboutPage";

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
              <Route path="/leaderboard" element={<LeaderboardPage />} />
              <Route path="/blog" element={<BlogPage />} />
              <Route path="/about" element={<AboutPage />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </ThemeContext.Provider>
  );
}

export default App;
