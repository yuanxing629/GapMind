import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

/**
* P0.5-4：使用 localStorage 持久化浅色/深色主题偏好。
* antd ConfigProvider 的 algorithm 在 main.tsx 中切换；html 的
* `data-theme` 属性驱动手写 CSS 覆盖规则。
 */

const STORAGE_KEY = "gm-theme";

interface ThemeContextValue {
  isDark: boolean;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue>({ isDark: false, toggleTheme: () => {} });

function readInitial(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "dark";
  } catch {
    return false;
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [isDark, setIsDark] = useState<boolean>(() => readInitial());

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, isDark ? "dark" : "light");
    } catch {
// 忽略存储失败（隐私模式/配额不足）。
    }
    document.documentElement.setAttribute("data-theme", isDark ? "dark" : "light");
  }, [isDark]);

  return (
    <ThemeContext.Provider value={{ isDark, toggleTheme: () => setIsDark((v) => !v) }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}
