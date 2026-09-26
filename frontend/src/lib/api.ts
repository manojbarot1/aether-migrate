import type { UserManager } from "oidc-client-ts";
import type {
  AssessmentRun,
  AuditPage,
  CatalogStatus,
  CompareResult,
  ClientConfig,
  Connection,
  InventorySummary,
  Me,
  Member,
  PolicyTemplate,
  ResourceDetail,
  ResourcePage,
  Role,
  Snapshot,
  TopologyView,
  Workspace,
} from "./types";

export class ApiError extends Error {
  status: number;
  code: string;
  requestId: string | null;
  details: unknown;

  constructor(status: number, code: string, message: string, requestId: string | null, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

export async function fetchClientConfig(): Promise<ClientConfig> {
  const r = await fetch("/api/v1/meta/client-config");
  if (!r.ok) throw new Error(`client config unavailable (${r.status})`);
  return (await r.json()) as ClientConfig;
}

type Json = Record<string, unknown> | unknown[];

export function createApi(manager: UserManager) {
  async function request<T>(method: string, path: string, body?: Json): Promise<T> {
    const user = await manager.getUser();
    const headers: Record<string, string> = { Accept: "application/json" };
    if (user?.access_token) headers.Authorization = `Bearer ${user.access_token}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const r = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    if (r.status === 401) {
      await manager.signinRedirect({ state: window.location.pathname });
      throw new ApiError(401, "unauthorized", "Session expired", null);
    }
    if (r.status === 204) return undefined as T;
    const data = (await r.json().catch(() => ({}))) as { error?: { code: string; message: string; request_id?: string; details?: unknown } };
    if (!r.ok) {
      const e = data.error;
      throw new ApiError(r.status, e?.code ?? "error", e?.message ?? `Request failed (${r.status})`, e?.request_id ?? null, e?.details);
    }
    return data as T;
  }

  const ws = (id: string) => `/api/v1/workspaces/${encodeURIComponent(id)}`;

  return {
    me: () => request<Me>("GET", "/api/v1/me"),
    createWorkspace: (slug: string, name: string) => request<Workspace>("POST", "/api/v1/workspaces", { slug, name }),

    members: (wsId: string) => request<Member[]>("GET", `${ws(wsId)}/members`),
    setMember: (wsId: string, email: string, role: Role) => request<Member>("PUT", `${ws(wsId)}/members`, { email, role }),
    removeMember: (wsId: string, userId: string) => request<void>("DELETE", `${ws(wsId)}/members/${userId}`),

    connections: (wsId: string) => request<Connection[]>("GET", `${ws(wsId)}/connections`),
    connection: (wsId: string, id: string) => request<Connection>("GET", `${ws(wsId)}/connections/${id}`),
    createConnection: (wsId: string, body: Json) => request<Connection>("POST", `${ws(wsId)}/connections`, body),
    updateConnection: (wsId: string, id: string, body: Json) =>
      request<Connection>("PATCH", `${ws(wsId)}/connections/${id}`, body),
    deleteConnection: (wsId: string, id: string) => request<void>("DELETE", `${ws(wsId)}/connections/${id}`),
    testConnection: (wsId: string, id: string) => request<Connection>("POST", `${ws(wsId)}/connections/${id}/test`),
    connectionSetup: (wsId: string, id: string) => request<PolicyTemplate>("GET", `${ws(wsId)}/connections/${id}/setup`),

    discover: (wsId: string, connectionId: string) =>
      request<Snapshot>("POST", `${ws(wsId)}/connections/${connectionId}/discover`),
    snapshots: (wsId: string, connectionId?: string) =>
      request<Snapshot[]>("GET", `${ws(wsId)}/snapshots${connectionId ? `?connection_id=${connectionId}` : ""}`),
    inventorySummary: (wsId: string) => request<InventorySummary>("GET", `${ws(wsId)}/inventory/summary`),
    resources: (wsId: string, params: Record<string, string | number | undefined>) => {
      const q = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
      return request<ResourcePage>("GET", `${ws(wsId)}/inventory/resources?${q.toString()}`);
    },
    topology: (wsId: string, networkId: string, securityGroups: boolean) =>
      request<TopologyView>("GET", `${ws(wsId)}/inventory/topology/${networkId}?security_groups=${securityGroups}`),
    resource: (wsId: string, id: string) => request<ResourceDetail>("GET", `${ws(wsId)}/inventory/resources/${id}`),

    catalogStatus: () => request<CatalogStatus>("GET", "/api/v1/catalog/status"),
    catalogSync: () => request<{ workflow_id: string }>("POST", "/api/v1/catalog/sync"),
    compare: (wsId: string, body: Json) => request<CompareResult>("POST", `${ws(wsId)}/compare`, body),

    assess: (wsId: string, body: Json) => request<AssessmentRun>("POST", `${ws(wsId)}/assessments`, body),
    assessments: (wsId: string) => request<AssessmentRun[]>("GET", `${ws(wsId)}/assessments`),
    assessment: (wsId: string, id: string) => request<AssessmentRun>("GET", `${ws(wsId)}/assessments/${id}`),
    acknowledge: (wsId: string, body: Json) => request<void>("PUT", `${ws(wsId)}/acknowledgements`, body),
    revokeAck: (wsId: string, nativeId: string, ruleId: string) =>
      request<void>("DELETE", `${ws(wsId)}/acknowledgements?native_id=${encodeURIComponent(nativeId)}&rule_id=${ruleId}`),

    audit: (wsId: string, params: { before?: number; action?: string; limit?: number }) => {
      const q = new URLSearchParams();
      if (params.before) q.set("before", String(params.before));
      if (params.action) q.set("action", params.action);
      q.set("limit", String(params.limit ?? 50));
      return request<AuditPage>("GET", `${ws(wsId)}/audit?${q.toString()}`);
    },
  };
}

export type Api = ReturnType<typeof createApi>;

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.requestId ? `${e.message} (request ${e.requestId.slice(0, 8)})` : e.message;
  if (e instanceof Error) return e.message;
  return "Unexpected error";
}
