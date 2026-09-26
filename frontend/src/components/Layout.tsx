import { useQuery } from "@tanstack/react-query";
import { Cable, LayoutDashboard, LogOut, Moon, ScrollText, Sun, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate, useParams } from "react-router";
import { useAuth } from "../lib/auth";
import { useApi } from "../lib/context";
import { Select } from "./ui";

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    try {
      const saved = localStorage.getItem("aether.theme");
      if (saved === "light" || saved === "dark") return saved;
    } catch {
      /* storage unavailable */
    }
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("aether.theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))] as const;
}

export function Layout() {
  const api = useApi();
  const { user, signOut } = useAuth();
  const { workspaceId } = useParams();
  const navigate = useNavigate();
  const [theme, toggleTheme] = useTheme();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });

  const memberships = me.data?.memberships ?? [];
  const current = memberships.find((m) => m.workspace.id === workspaceId);

  const nav = [
    { to: "", label: "Overview", icon: LayoutDashboard, end: true },
    { to: "connections", label: "Connections", icon: Cable },
    { to: "audit", label: "Audit log", icon: ScrollText },
    { to: "members", label: "Members", icon: Users },
  ];

  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col border-b border-[var(--border)] bg-[var(--panel)] md:w-60 md:border-r md:border-b-0">
        <div className="flex items-center gap-2 px-4 py-4">
          <img src="/favicon.svg" alt="" className="size-7" />
          <div>
            <div className="text-sm font-semibold tracking-wide">AETHER MIGRATE</div>
            <div className="text-xs text-[var(--muted)]">Migration control plane</div>
          </div>
        </div>
        <div className="px-3 pb-3">
          <label className="sr-only" htmlFor="ws-switch">
            Workspace
          </label>
          <Select
            id="ws-switch"
            value={workspaceId ?? ""}
            onChange={(e) => navigate(e.target.value ? `/w/${e.target.value}` : "/")}
          >
            {!current && <option value="">Select workspace…</option>}
            {memberships.map((m) => (
              <option key={m.workspace.id} value={m.workspace.id}>
                {m.workspace.name}
              </option>
            ))}
          </Select>
          {current && <div className="mt-1 px-1 text-xs text-[var(--muted)]">Your role: {current.role}</div>}
        </div>
        {workspaceId && (
          <nav className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:overflow-visible">
            {nav.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={label}
                to={`/w/${workspaceId}/${to}`}
                end={end}
                className={({ isActive }) =>
                  `flex items-center gap-2 rounded-md px-3 py-2 text-sm whitespace-nowrap ${
                    isActive ? "bg-[var(--panel-2)] font-medium text-[var(--text)]" : "text-[var(--muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]"
                  }`
                }
              >
                <Icon className="size-4" aria-hidden /> {label}
              </NavLink>
            ))}
          </nav>
        )}
        <div className="mt-auto hidden border-t border-[var(--border)] p-3 md:block">
          <div className="truncate text-sm font-medium">{user?.profile.name ?? user?.profile.email}</div>
          <div className="truncate text-xs text-[var(--muted)]">
            {user?.profile.email} {me.data?.is_platform_admin && "· platform admin"}
          </div>
          <div className="mt-2 flex gap-1">
            <button
              onClick={toggleTheme}
              className="rounded-md p-1.5 text-[var(--muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]"
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            >
              {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </button>
            <button
              onClick={() => void signOut()}
              className="flex items-center gap-1 rounded-md px-2 py-1.5 text-xs text-[var(--muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]"
            >
              <LogOut className="size-4" /> Sign out
            </button>
          </div>
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto px-4 py-6 md:px-8">
        <div className="mx-auto max-w-6xl">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
