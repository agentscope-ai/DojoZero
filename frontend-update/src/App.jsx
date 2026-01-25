import { useState, createContext, useContext, Suspense, lazy } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import TopBar from "./components/TopBar";
import GamesPage from "./pages/GamesPage";
import LeaderboardPage from "./pages/LeaderboardPage";
import BlogPage from "./pages/BlogPage";
import AboutPage from "./pages/AboutPage";
import { DataSourceProvider } from "./hooks/useDataSource.jsx";

// Lazy load RoomPage for better initial load performance
const RoomPage = lazy(() => import("./pages/RoomPage"));

// Theme context
export const ThemeContext = createContext();

export function useTheme() {
  return useContext(ThemeContext);
}

// Loading fallback for lazy-loaded pages
function PageLoader() {
  return (
    <div style={{
      height: "100vh",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      background: "var(--bg-primary)",
    }}>
      <div style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 16,
      }}>
        <div style={{
          width: 48,
          height: 48,
          border: "3px solid var(--border-default)",
          borderTopColor: "var(--accent-primary)",
          borderRadius: "50%",
          animation: "spin 1s linear infinite",
        }} />
        <span style={{
          color: "var(--text-secondary)",
          fontSize: 14,
          letterSpacing: "0.1em",
        }}>
          LOADING...
        </span>
      </div>
    </div>
  );
}

// Main layout with TopBar (for most pages)
function MainLayout({ children }) {
  return (
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
        {children}
      </main>
    </div>
  );
}

// Full-screen layout without TopBar (for Room page)
function FullScreenLayout({ children }) {
  return (
    <div
      style={{
        height: "100vh",
        background: "var(--bg-primary)",
        overflow: "hidden",
      }}
    >
      {children}
    </div>
  );
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
          <Suspense fallback={<PageLoader />}>
            <Routes>
              {/* Room page - full screen, no TopBar */}
              <Route
                path="/games/:gameId"
                element={
                  <FullScreenLayout>
                    <RoomPage />
                  </FullScreenLayout>
                }
              />
              
              {/* Main pages with TopBar */}
              <Route
                path="/*"
                element={
                  <MainLayout>
                    <Routes>
                      <Route path="/" element={<GamesPage />} />
                      <Route path="/games" element={<GamesPage />} />
                      <Route path="/leaderboard" element={<LeaderboardPage />} />
                      <Route path="/blog" element={<BlogPage />} />
                      <Route path="/about" element={<AboutPage />} />
                    </Routes>
                  </MainLayout>
                }
              />
            </Routes>
          </Suspense>
        </BrowserRouter>
      </DataSourceProvider>
    </ThemeContext.Provider>
  );
}

export default App;
