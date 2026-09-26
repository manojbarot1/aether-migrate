import { useQuery } from "@tanstack/react-query";
import { Cable, Calculator, ChevronsUpDown, LayoutDashboard, LogOut, Moon, Network, Radar, ScrollText, Search, Server, Sun, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams } from "react-router";
import { useAuth } from "../lib/auth";
import { useApi } from "../lib/context";
import { CommandPalette, type PaletteLink } from "./CommandPalette";

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

const SECTIONS = [
  {
    title: "Estate",
    items: [
      { to: "", label: "Overview", icon: LayoutDashboard, end: true },
      { to: "inventory", label: "Inventory", icon: Server },
      { to: "topology", label: "Topology", icon: Network },
      { to: "compare", label: "Cost comparison", icon: Calculator },
    ],
  },
  {
    title: "Operations",
    items: [
      { to: "discovery", label: "Discovery runs", icon: Radar },
      { to: "connections", label: "Connections", icon: Cable },
    ],
  },
  {
    title: "Governance",
    items: [
      { to: "members", label: "Members", icon: Users },
      { to: "audit", label: "Audit log", icon: ScrollText },
    ],
  },
] as const;

const initials = (s: string) =>
  s
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join("");

export function Layout() {
  const api = useApi();
  const { user, signOut } = useAuth();
  const { workspaceId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [theme, toggleTheme] = useTheme();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });

  const memberships = me.data?.memberships ?? [];
  const current = memberships.find((m) => m.workspace.id === workspaceId);
  const who = user?.profile.name ?? user?.profile.email ?? "";

  const allItems = SECTIONS.flatMap((s) => s.items.map((i) => ({ ...i, section: s.title })));
  const segment = location.pathname.split("/")[3] ?? "";
  const page = allItems.find((i) => i.to === segment);
  const links: PaletteLink[] = workspaceId
    ? allItems.map((i) => ({ label: i.label, to: `/w/${workspaceId}/${i.to}`, icon: i.icon, group: "Pages" }))
    : [];

  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col bg-[var(--rail)] text-[var(--rail-text)] md:w-60">
        <Link to="/" className="flex items-center gap-2.5 px-4 pt-4 pb-3">
          <img src="/favicon.svg" alt="" className="size-7" />
          <div className="leading-tight">
            <div className="text-[13px] font-semibold tracking-[0.08em] text-white">AETHER MIGRATE</div>
            <div className="text-[11px] text-[var(--rail-muted)]">Migration control plane</div>
          </div>
        </Link>

        <div className="px-3 pb-3">
          <label className="relative block">
            <span className="sr-only">Workspace</span>
            <select
              value={workspaceId ?? ""}
              onChange={(e) => navigate(e.target.value ? `/w/${e.target.value}` : "/")}
              className="w-full appearance-none rounded-md border border-white/10 bg-[var(--rail-2)] py-2 pr-8 pl-3 text-sm text-white focus:border-[var(--accent)] focus:outline-none"
            >
              {!current && <option value="">Select workspace…</option>}
              {memberships.map((m) => (
                <option key={m.workspace.id} value={m.workspace.id}>
                  {m.workspace.name}
                </option>
              ))}
            </select>
            <ChevronsUpDown className="pointer-events-none absolute top-2.5 right-2.5 size-4 text-[var(--rail-muted)]" />
          </label>
          {current && <div className="mt-1.5 px-1 text-[11px] text-[var(--rail-muted)]">Signed in as {current.role}</div>}
        </div>

        {workspaceId && (
          <nav className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:gap-4 md:overflow-visible">
            {SECTIONS.map((section) => (
              <div key={section.title} className="flex gap-1 md:flex-col md:gap-0.5">
                <div className="hidden px-3 pb-1 text-[10px] font-semibold tracking-[0.12em] text-[var(--rail-muted)] uppercase md:block">
                  {section.title}
                </div>
                {section.items.map(({ to, label, icon: Icon, ...rest }) => (
                  <NavLink
                    key={label}
                    to={`/w/${workspaceId}/${to}`}
                    end={"end" in rest}
                    className={({ isActive }) =>
                      `relative flex items-center gap-2.5 rounded-md px-3 py-2 text-sm whitespace-nowrap transition-colors ${
                        isActive ? "bg-white/10 font-medium text-[var(--rail-active)]" : "text-[var(--rail-text)] hover:bg-white/5 hover:text-white"
                      }`
                    }
                  >
                    {({ isActive }) => (
                      <>
                        {isActive && <span className="absolute top-1.5 bottom-1.5 left-0 hidden w-0.5 rounded bg-[var(--accent)] md:block" />}
                        <Icon className="size-4" aria-hidden /> {label}
                      </>
                    )}
                  </NavLink>
                ))}
              </div>
            ))}
          </nav>
        )}

        <div className="mt-auto hidden border-t border-white/10 p-3 md:block">
          <div className="flex items-center gap-2.5">
            <div className="flex size-8 items-center justify-center rounded-full bg-[var(--accent)] text-xs font-semibold text-white">{initials(who)}</div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm text-white">{who}</div>
              <div className="truncate text-[11px] text-[var(--rail-muted)]">{me.data?.is_platform_admin ? "Platform administrator" : user?.profile.email}</div>
            </div>
            <button onClick={() => void signOut()} className="rounded p-1.5 text-[var(--rail-muted)] hover:bg-white/10 hover:text-white" aria-label="Sign out" title="Sign out">
              <LogOut className="size-4" />
            </button>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-[var(--border)] bg-[var(--panel)] px-4 md:px-8">
          <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-sm">
            <Link to="/" className="text-[var(--muted)] hover:text-[var(--text)]">
              Workspaces
            </Link>
            {current && (
              <>
                <span className="text-[var(--muted)]">/</span>
                <Link to={`/w/${current.workspace.id}`} className="truncate text-[var(--muted)] hover:text-[var(--text)]">
                  {current.workspace.name}
                </Link>
              </>
            )}
            {page && page.to !== "" && (
              <>
                <span className="text-[var(--muted)]">/</span>
                <span className="truncate font-medium">{page.label}</span>
              </>
            )}
          </nav>
          <button
            onClick={() => window.dispatchEvent(new Event("aether:palette"))}
            className="ml-auto flex w-full max-w-xs items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-1.5 text-sm text-[var(--muted)] hover:border-[var(--accent)]"
          >
            <Search className="size-4" aria-hidden />
            <span className="flex-1 text-left">Search or jump to…</span>
            <kbd className="rounded border border-[var(--border)] bg-[var(--panel)] px-1.5 text-[10px]">Ctrl K</kbd>
          </button>
          <button
            onClick={toggleTheme}
            className="rounded-md p-2 text-[var(--muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]"
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          >
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </button>
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto px-4 py-6 md:px-8">
          <div className="mx-auto max-w-7xl">
            <Outlet />
          </div>
        </main>
      </div>
      <CommandPalette workspaceId={workspaceId} links={links} />
    </div>
  );
}
