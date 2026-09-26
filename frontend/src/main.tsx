import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useMemo, type ComponentType } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import type { UserManager } from "oidc-client-ts";
import { Layout } from "./components/Layout";
import { ApiError, createApi, fetchClientConfig } from "./lib/api";
import { AuthProvider, FullPageMessage, createUserManager } from "./lib/auth";
import { ApiContext } from "./lib/context";
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

// Each page is its own chunk: the shell loads fast and heavy pages (React Flow, ELK)
// are fetched only when visited.
const page =
  <M, K extends keyof M>(load: () => Promise<M>, name: K) =>
  async () => ({ Component: (await load())[name] as ComponentType });

const router = createBrowserRouter([
  {
    element: <Layout />,
    hydrateFallbackElement: <FullPageMessage title="Loading…" />,
    children: [
      { index: true, lazy: page(() => import("./pages/Home"), "Home") },
      { path: "signin-callback", lazy: page(() => import("./pages/Home"), "Home") },
      {
        path: "w/:workspaceId",
        children: [
          { index: true, lazy: page(() => import("./pages/Overview"), "Overview") },
          { path: "inventory", lazy: page(() => import("./pages/Inventory"), "Inventory") },
          { path: "inventory/:resourceId", lazy: page(() => import("./pages/ResourceDetail"), "ResourceDetail") },
          { path: "topology", lazy: page(() => import("./pages/Topology"), "Topology") },
          { path: "compare", lazy: page(() => import("./pages/Compare"), "Compare") },
          { path: "assessment", lazy: page(() => import("./pages/Assessment"), "Assessment") },
          { path: "assessment/:runId", lazy: page(() => import("./pages/Assessment"), "Assessment") },
          { path: "discovery", lazy: page(() => import("./pages/Discovery"), "Discovery") },
          { path: "connections", lazy: page(() => import("./pages/Connections"), "Connections") },
          { path: "audit", lazy: page(() => import("./pages/Audit"), "Audit") },
          { path: "members", lazy: page(() => import("./pages/Members"), "Members") },
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
