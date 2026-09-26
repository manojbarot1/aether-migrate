export type Role = "viewer" | "analyst" | "connection-admin" | "approver" | "operator" | "admin";
export const ROLES: Role[] = ["viewer", "analyst", "connection-admin", "approver", "operator", "admin"];
export const roleAtLeast = (role: Role | undefined, min: Role) =>
  role !== undefined && ROLES.indexOf(role) >= ROLES.indexOf(min);

export interface Workspace {
  id: string;
  slug: string;
  name: string;
  settings: Record<string, unknown>;
  created_at: string;
}

export interface Me {
  id: string;
  email: string | null;
  display_name: string | null;
  is_platform_admin: boolean;
  memberships: { workspace: Workspace; role: Role }[];
}

export interface Member {
  user_id: string;
  email: string | null;
  display_name: string | null;
  role: Role;
}

export type ConnectionStatus = "untested" | "ok" | "warning" | "error";
export type CheckStatus = "pass" | "warn" | "fail" | "skipped";

export interface CheckResult {
  id: string;
  status: CheckStatus;
  message: string;
  details: Record<string, unknown>;
}

export interface ConnectionTestResult {
  status: ConnectionStatus;
  identity: Record<string, string>;
  checks: CheckResult[];
  cloud_calls: string[];
  tested_at: string;
}

export interface Connection {
  id: string;
  workspace_id: string;
  name: string;
  provider: "aws" | "azure" | "gcp" | "ibm";
  mode: "read_only" | "execute";
  auth_method: "aws_assume_role" | "aws_access_key";
  config: { role_arn?: string; external_id?: string; regions?: string[]; home_region?: string };
  has_secret: boolean;
  secret_version: number | null;
  status: ConnectionStatus;
  last_tested_at: string | null;
  last_test_result: ConnectionTestResult | null;
  created_at: string;
  updated_at: string;
}

export interface PolicyTemplate {
  provider: string;
  auth_method: string;
  external_id: string | null;
  trust_policy: Record<string, unknown> | null;
  permissions_policy: Record<string, unknown>;
  instructions: string[];
}

export interface AuditEvent {
  seq: number;
  id: string;
  occurred_at: string;
  actor_type: string;
  actor_id: string | null;
  actor_display: string | null;
  workspace_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  connection_id: string | null;
  status: "success" | "denied" | "failure";
  details: Record<string, unknown>;
  request_id: string | null;
  hash: string;
}

export interface AuditPage {
  items: AuditEvent[];
  next_before: number | null;
}

export interface ClientConfig {
  oidc_authority: string;
  oidc_client_id: string;
  version: string;
  env: string;
}

export interface CoverageEntry {
  region: string;
  kind: string;
  status: "ok" | "denied" | "disabled" | "throttled_partial" | "error";
  detail?: string | null;
}

export interface Snapshot {
  id: string;
  connection_id: string;
  provider: string;
  status: "running" | "complete" | "partial" | "failed";
  started_at: string;
  finished_at: string | null;
  regions: string[] | null;
  coverage: CoverageEntry[] | null;
  stats: { resources?: Record<string, number>; regions?: number; cloud_calls?: number } | null;
  error: string | null;
}

export interface ResourceSummary {
  id: string;
  type: string;
  native_id: string;
  name: string | null;
  provider: string;
  account: string;
  region: string;
  zone: string | null;
  status: string | null;
  tags: Record<string, string>;
  vcpu: number | null;
  memory_mib: number | null;
  cpu_arch: string | null;
  os_family: string | null;
  source_sku: string | null;
  snapshot_id: string;
  connection_id: string;
  discovered_at: string;
}

export interface ResourcePage {
  items: ResourceSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface ResourceDetail extends ResourceSummary {
  spec: Record<string, unknown>;
  raw: Record<string, unknown>;
  created_at_source: string | null;
  neighbours: { direction: "in" | "out"; kind: string; resource: ResourceSummary }[];
  snapshot: Snapshot;
}

export interface InventorySummary {
  resources_by_type: Record<string, number>;
  vms_by_region: { region: string; count: number }[];
  total_vcpu: number;
  total_memory_mib: number;
}

export interface TopologyNode {
  id: string;
  type: "network" | "subnet" | "vm" | "load_balancer" | "security_group";
  native_id: string;
  name: string | null;
  parent: string | null;
  status: string | null;
  detail: string | null;
}

export interface TopologyView {
  network: TopologyNode;
  nodes: TopologyNode[];
  edges: { from: string; to: string; kind: "routes_to" | "protected_by" | "references" }[];
  truncated: boolean;
  mermaid: string;
}
