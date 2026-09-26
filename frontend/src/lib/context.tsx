import { useQuery } from "@tanstack/react-query";
import { createContext, useContext } from "react";
import { useParams } from "react-router";
import type { Api } from "./api";
import { roleAtLeast, type Role } from "./types";

export const ApiContext = createContext<Api | null>(null);

export function useApi(): Api {
  const api = useContext(ApiContext);
  if (!api) throw new Error("useApi outside ApiContext");
  return api;
}

/** The current workspace and the caller's role in it. */
export function useWorkspace() {
  const api = useApi();
  const { workspaceId } = useParams();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const membership = me.data?.memberships.find((m) => m.workspace.id === workspaceId);
  const role = membership?.role;
  return {
    workspaceId: workspaceId ?? "",
    workspace: membership?.workspace,
    role,
    can: (min: Role) => roleAtLeast(role, min),
    loading: me.isLoading,
  };
}
