import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useMemo } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import type { UserManager } from "oidc-client-ts";
import { Layout } from "./components/Layout";
import { ApiError, createApi, fetchClientConfig } from "./lib/api";
import { AuthProvider, FullPageMessage, createUserManager } from "./lib/auth";
import { ApiContext } from "./lib/context";
import { Audit } from "./pages/Audit";
import { Connections } from "./pages/Connections";
import { Discovery } from "./pages/Discovery";
import { Inventory } from "./pages/Inventory";
import { ResourceDetail } from "./pages/ResourceDetail";
import { Home } from "./pages/Home";
import { Members } from "./pages/Members";
import { Overview } from "./pages/Overview";
import "./index.css";

try {
  const t = localStorage.getItem("aether.theme");
  document.documentElement.dataset.theme = t === "dark" || t === "light" ? t : matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
} catch {
  /* storage unavailable */
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: (count, e) => !(e instanceof ApiError && e.status < 500) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
});

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { index: true, element: <Home /> },
      { path: "signin-callback", element: <Home /> },
      {
        path: "w/:workspaceId",
        children: [
          { index: true, element: <Overview /> },
          { path: "inventory", element: <Inventory /> },
          { path: "inventory/:resourceId", element: <ResourceDetail /> },
          { path: "discovery", element: <Discovery /> },
          { path: "connections", element: <Connections /> },
          { path: "audit", element: <Audit /> },
          { path: "members", element: <Members /> },
        ],
      },
      { path: "*", element: <FullPageMessage title="Page not found" /> },
    ],
  },
]);

function App({ manager }: { manager: UserManager }) {
  const api = useMemo(() => createApi(manager), [manager]);
  return (
    <AuthProvider manager={manager}>
      <ApiContext.Provider value={api}>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </ApiContext.Provider>
    </AuthProvider>
  );
}

const root = createRoot(document.getElementById("root")!);
fetchClientConfig()
  .then((cfg) => {
    root.render(
      <StrictMode>
        <App manager={createUserManager(cfg)} />
      </StrictMode>,
    );
  })
  .catch((e: unknown) => {
    root.render(<FullPageMessage title="AETHER MIGRATE is unavailable" detail={e instanceof Error ? e.message : String(e)} />);
  });
