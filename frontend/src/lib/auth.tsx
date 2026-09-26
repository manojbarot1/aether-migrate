import { UserManager, WebStorageStateStore, type User } from "oidc-client-ts";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { ClientConfig } from "./types";

export const CALLBACK_PATH = "/signin-callback";

export function createUserManager(cfg: ClientConfig): UserManager {
  const origin = window.location.origin;
  return new UserManager({
    authority: cfg.oidc_authority,
    client_id: cfg.oidc_client_id,
    redirect_uri: origin + CALLBACK_PATH,
    post_logout_redirect_uri: origin + "/",
    response_type: "code",
    scope: "openid profile email",
    automaticSilentRenew: true,
    // Session storage: tokens do not outlive the browser tab.
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  });
}

interface AuthState {
  user: User | null;
  manager: UserManager;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

// The authorization code is single-use: make sure the callback is processed once,
// even if the effect runs twice (React StrictMode in development).
let callbackOnce: Promise<User> | null = null;

export function AuthProvider({ manager, children }: { manager: UserManager; children: ReactNode }) {
  const [user, setUser] = useState<User | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (window.location.pathname === CALLBACK_PATH) {
        callbackOnce ??= manager.signinRedirectCallback();
        const u = await callbackOnce;
        const target = typeof u.state === "string" && u.state.startsWith("/") ? u.state : "/";
        window.history.replaceState({}, "", target);
        if (!cancelled) setUser(u);
        return;
      }
      const existing = await manager.getUser();
      if (existing && !existing.expired) {
        if (!cancelled) setUser(existing);
        return;
      }
      await manager.signinRedirect({ state: window.location.pathname + window.location.search });
    };
    load().catch((e: unknown) => {
      console.error("sign-in failed", e);
      if (!cancelled) setUser(null);
    });
    const onLoaded = (u: User) => setUser(u);
    const onExpired = () => void manager.signinRedirect({ state: window.location.pathname });
    manager.events.addUserLoaded(onLoaded);
    manager.events.addAccessTokenExpired(onExpired);
    return () => {
      cancelled = true;
      manager.events.removeUserLoaded(onLoaded);
      manager.events.removeAccessTokenExpired(onExpired);
    };
  }, [manager]);

  if (user === undefined) return <FullPageMessage title="Signing in…" />;
  if (user === null)
    return (
      <FullPageMessage title="Sign-in failed" detail="The identity provider could not be reached or rejected the request.">
        <button
          className="mt-4 rounded-md bg-[var(--accent)] px-4 py-2 text-sm font-medium text-[var(--accent-fg)]"
          onClick={() => void manager.signinRedirect()}
        >
          Try again
        </button>
      </FullPageMessage>
    );

  return (
    <AuthContext.Provider value={{ user, manager, signOut: () => manager.signoutRedirect() }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}

export function FullPageMessage({ title, detail, children }: { title: string; detail?: string; children?: ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="max-w-md text-center">
        <div className="text-lg font-semibold">{title}</div>
        {detail && <p className="mt-2 text-sm text-[var(--muted)]">{detail}</p>}
        {children}
      </div>
    </div>
  );
}
