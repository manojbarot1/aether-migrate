import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cable, Copy, FileKey2, Play, Plus, RotateCw, Trash2 } from "lucide-react";
import { Fragment, useState, type FormEvent } from "react";
import {
  Badge,
  Button,
  Card,
  Code,
  Dialog,
  EmptyState,
  ErrorBanner,
  Field,
  Input,
  PageHeader,
  Spinner,
  StatusBadge,
  Table,
  relativeTime,
} from "../components/ui";
import { errorMessage } from "../lib/api";
import { useApi, useWorkspace } from "../lib/context";
import type { Connection } from "../lib/types";

const parseRegions = (s: string) =>
  s
    .split(/[\s,]+/)
    .map((r) => r.trim())
    .filter(Boolean);

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button
      variant="ghost"
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        });
      }}
    >
      <Copy className="size-4" /> {done ? "Copied" : "Copy"}
    </Button>
  );
}

function CreateConnectionDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (c: Connection) => void }) {
  const api = useApi();
  const { workspaceId } = useWorkspace();
  const [method, setMethod] = useState<"aws_assume_role" | "aws_access_key">("aws_assume_role");
  const [name, setName] = useState("");
  const [roleArn, setRoleArn] = useState("");
  const [regions, setRegions] = useState("");
  const [homeRegion, setHomeRegion] = useState("us-east-1");
  const [akid, setAkid] = useState("");
  const [secret, setSecret] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api.createConnection(workspaceId, {
        name,
        provider: "aws",
        mode: "read_only",
        config:
          method === "aws_assume_role"
            ? { auth_method: method, role_arn: roleArn.trim(), regions: parseRegions(regions), home_region: homeRegion }
            : { auth_method: method, regions: parseRegions(regions), home_region: homeRegion },
        ...(method === "aws_access_key" ? { secret: { access_key_id: akid.trim(), secret_access_key: secret } } : {}),
      }),
    onSuccess: (c) => {
      setSecret("");
      setAkid("");
      onCreated(c);
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  return (
    <Dialog open={open} onClose={onClose} title="New AWS connection">
      <form onSubmit={submit} className="space-y-4">
        <ErrorBanner error={create.error ? errorMessage(create.error) : null} />
        <fieldset className="grid grid-cols-2 gap-2">
          <legend className="mb-1 text-sm font-medium">Authentication</legend>
          {(
            [
              ["aws_assume_role", "Assume IAM role", "Recommended: short-lived credentials, no stored secret."],
              ["aws_access_key", "Access key", "Long-lived key stored in OpenBao. Use only if roles are impossible."],
            ] as const
          ).map(([value, label, hint]) => (
            <label
              key={value}
              className={`cursor-pointer rounded-md border p-3 text-sm ${method === value ? "border-[var(--accent)]" : "border-[var(--border)]"}`}
            >
              <input type="radio" name="method" className="sr-only" checked={method === value} onChange={() => setMethod(value)} />
              <div className="font-medium">{label}</div>
              <div className="mt-1 text-xs text-[var(--muted)]">{hint}</div>
            </label>
          ))}
        </fieldset>
        <Field label="Connection name">
          <Input required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} placeholder="aws-prod-eu" />
        </Field>
        {method === "aws_assume_role" && (
          <Field label="IAM role ARN" hint="Create the role after saving: the next screen shows its trust and permissions policies.">
            <Input required value={roleArn} onChange={(e) => setRoleArn(e.target.value)} placeholder="arn:aws:iam::123456789012:role/AetherMigrateReadOnly" />
          </Field>
        )}
        {method === "aws_access_key" && (
          <>
            <Field label="Access key ID">
              <Input required autoComplete="off" value={akid} onChange={(e) => setAkid(e.target.value)} placeholder="AKIA…" />
            </Field>
            <Field label="Secret access key" hint="Sent once over TLS to the secret store. It is never shown again.">
              <Input required type="password" autoComplete="new-password" value={secret} onChange={(e) => setSecret(e.target.value)} />
            </Field>
          </>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Discovery regions" hint="Comma-separated. Empty = all enabled regions.">
            <Input value={regions} onChange={(e) => setRegions(e.target.value)} placeholder="eu-central-1, eu-west-1" />
          </Field>
          <Field label="Home region" hint="Used for STS and account-level calls.">
            <Input required value={homeRegion} onChange={(e) => setHomeRegion(e.target.value)} />
          </Field>
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={create.isPending}>
            Save connection
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function SetupDialog({ connection, onClose }: { connection: Connection | null; onClose: () => void }) {
  const api = useApi();
  const { workspaceId } = useWorkspace();
  const setup = useQuery({
    queryKey: ["setup", connection?.id],
    queryFn: () => api.connectionSetup(workspaceId, connection!.id),
    enabled: connection !== null,
  });
  const trust = setup.data?.trust_policy ? JSON.stringify(setup.data.trust_policy, null, 2) : null;
  const perms = setup.data ? JSON.stringify(setup.data.permissions_policy, null, 2) : "";
  return (
    <Dialog open={connection !== null} onClose={onClose} title={`Set up ${connection?.name ?? ""}`} wide>
      {setup.isLoading && <Spinner />}
      <ErrorBanner error={setup.error ? errorMessage(setup.error) : null} />
      {setup.data && (
        <div className="space-y-5 text-sm">
          <ol className="list-decimal space-y-1 pl-5 text-[var(--muted)]">
            {setup.data.instructions.map((i) => (
              <li key={i}>{i}</li>
            ))}
          </ol>
          {setup.data.external_id && (
            <div>
              <div className="mb-1 font-medium">External ID</div>
              <div className="flex items-center gap-2">
                <code className="rounded bg-[var(--panel-2)] px-2 py-1 font-mono text-xs">{setup.data.external_id}</code>
                <CopyButton text={setup.data.external_id} />
              </div>
            </div>
          )}
          {trust && (
            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="font-medium">Trust policy</span>
                <CopyButton text={trust} />
              </div>
              <Code>{trust}</Code>
            </div>
          )}
          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="font-medium">Permissions policy (read-only)</span>
              <CopyButton text={perms} />
            </div>
            <Code>{perms}</Code>
          </div>
        </div>
      )}
    </Dialog>
  );
}

function RotateDialog({ connection, onClose }: { connection: Connection | null; onClose: () => void }) {
  const api = useApi();
  const qc = useQueryClient();
  const { workspaceId } = useWorkspace();
  const [akid, setAkid] = useState("");
  const [secret, setSecret] = useState("");
  const rotate = useMutation({
    mutationFn: () => api.updateConnection(workspaceId, connection!.id, { secret: { access_key_id: akid.trim(), secret_access_key: secret } }),
    onSuccess: async () => {
      setAkid("");
      setSecret("");
      await qc.invalidateQueries({ queryKey: ["connections", workspaceId] });
      onClose();
    },
  });
  return (
    <Dialog open={connection !== null} onClose={onClose} title={`Rotate key for ${connection?.name ?? ""}`}>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          rotate.mutate();
        }}
      >
        <ErrorBanner error={rotate.error ? errorMessage(rotate.error) : null} />
        <Field label="New access key ID">
          <Input required autoComplete="off" value={akid} onChange={(e) => setAkid(e.target.value)} />
        </Field>
        <Field label="New secret access key">
          <Input required type="password" autoComplete="new-password" value={secret} onChange={(e) => setSecret(e.target.value)} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={rotate.isPending}>
            Rotate
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function TestResult({ c }: { c: Connection }) {
  const r = c.last_test_result;
  if (!r) return <p className="text-sm text-[var(--muted)]">Not tested yet.</p>;
  return (
    <div className="space-y-3 text-sm">
      {r.identity.arn && (
        <div className="text-[var(--muted)]">
          Identity: <span className="font-mono text-xs text-[var(--text)]">{r.identity.arn}</span>
        </div>
      )}
      <ul className="space-y-2">
        {r.checks.map((ch) => (
          <li key={ch.id} className="flex items-start gap-2">
            <StatusBadge status={ch.status} />
            <div>
              <div>{ch.message}</div>
              {Array.isArray(ch.details.missing) && ch.details.missing.length > 0 && (
                <div className="mt-1 font-mono text-xs text-[var(--muted)]">{(ch.details.missing as string[]).join(", ")}</div>
              )}
            </div>
          </li>
        ))}
      </ul>
      <details className="text-xs text-[var(--muted)]">
        <summary className="cursor-pointer">{r.cloud_calls.length} cloud API calls (audited)</summary>
        <ul className="mt-1 font-mono">
          {r.cloud_calls.map((call, i) => (
            <li key={i}>{call}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

export function Connections() {
  const api = useApi();
  const qc = useQueryClient();
  const { workspaceId, can } = useWorkspace();
  const canManage = can("connection-admin");
  const [creating, setCreating] = useState(false);
  const [setupFor, setSetupFor] = useState<Connection | null>(null);
  const [rotateFor, setRotateFor] = useState<Connection | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const conns = useQuery({ queryKey: ["connections", workspaceId], queryFn: () => api.connections(workspaceId) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["connections", workspaceId] });

  const test = useMutation({
    mutationFn: (id: string) => api.testConnection(workspaceId, id),
    onSuccess: async (c) => {
      setExpanded(c.id);
      await refresh();
    },
    onError: (e) => setError(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteConnection(workspaceId, id),
    onSuccess: refresh,
    onError: (e) => setError(errorMessage(e)),
  });

  return (
    <>
      <PageHeader
        title="Cloud connections"
        subtitle="Read-only credentials. Secrets live in OpenBao and are readable only by the isolated connector worker."
        actions={
          canManage && (
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus className="size-4" /> New connection
            </Button>
          )
        }
      />
      <ErrorBanner error={error ?? (conns.error ? errorMessage(conns.error) : null)} onDismiss={() => setError(null)} />
      <Card>
        {conns.isLoading ? (
          <Spinner />
        ) : (conns.data ?? []).length === 0 ? (
          <EmptyState icon={<Cable className="size-8" />} title="No connections">
            {canManage ? "Add an AWS account to begin. Azure, GCP and IBM Cloud follow in later releases." : "A connection administrator can add cloud accounts."}
          </EmptyState>
        ) : (
          <Table head={["Name", "Provider", "Auth", "Scope", "Status", "Last test", ""]}>
            {conns.data!.map((c) => (
              <Fragment key={c.id}>
                <tr className="border-b border-[var(--border)] align-middle">
                  <td className="px-3 py-2">
                    <button className="font-medium hover:text-[var(--accent)]" onClick={() => setExpanded(expanded === c.id ? null : c.id)}>
                      {c.name}
                    </button>
                  </td>
                  <td className="px-3 py-2">
                    <Badge tone="accent">{c.provider.toUpperCase()}</Badge>
                  </td>
                  <td className="px-3 py-2 text-[var(--muted)]">{c.auth_method === "aws_assume_role" ? "Assume role" : `Access key v${c.secret_version ?? "?"}`}</td>
                  <td className="px-3 py-2 text-[var(--muted)]">{c.config.regions?.length ? c.config.regions.join(", ") : "all regions"}</td>
                  <td className="px-3 py-2">
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="px-3 py-2 text-[var(--muted)]">{relativeTime(c.last_tested_at)}</td>
                  <td className="px-3 py-2">
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" onClick={() => setSetupFor(c)} title="Setup instructions">
                        <FileKey2 className="size-4" /> Setup
                      </Button>
                      {canManage && (
                        <>
                          <Button variant="ghost" busy={test.isPending && test.variables === c.id} onClick={() => test.mutate(c.id)}>
                            <Play className="size-4" /> Test
                          </Button>
                          {c.auth_method === "aws_access_key" && (
                            <Button variant="ghost" onClick={() => setRotateFor(c)}>
                              <RotateCw className="size-4" /> Rotate
                            </Button>
                          )}
                          <Button
                            variant="ghost"
                            aria-label={`Delete ${c.name}`}
                            onClick={() => {
                              if (window.confirm(`Delete connection "${c.name}"? Its stored secret is destroyed.`)) remove.mutate(c.id);
                            }}
                          >
                            <Trash2 className="size-4 text-[var(--err)]" />
                          </Button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
                {expanded === c.id && (
                  <tr className="border-b border-[var(--border)] bg-[var(--panel-2)]">
                    <td colSpan={7} className="px-4 py-3">
                      <TestResult c={c} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </Table>
        )}
      </Card>
      <CreateConnectionDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(c) => {
          setCreating(false);
          void refresh();
          if (c.auth_method === "aws_assume_role") setSetupFor(c);
        }}
      />
      <SetupDialog connection={setupFor} onClose={() => setSetupFor(null)} />
      <RotateDialog connection={rotateFor} onClose={() => setRotateFor(null)} />
    </>
  );
}
