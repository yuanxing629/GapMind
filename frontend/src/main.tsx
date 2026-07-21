import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App as AntdApp, ConfigProvider, theme as antdTheme } from "antd";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <ConfigProvider
          theme={{
            algorithm: antdTheme.defaultAlgorithm,
            token: {
              colorPrimary: "#1677ff",
            },
          }}
        >
          <AntdApp>
            <App />
          </AntdApp>
        </ConfigProvider>
      </BrowserRouter>
    </ErrorBoundary>
  </React.StrictMode>
);
