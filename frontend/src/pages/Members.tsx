import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Button, Card, ErrorBanner, Field, Input, PageHeader, Select, Spinner, Table } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useApi, useWorkspace } from "../lib/context";
import { ROLES, type Role } from "../lib/types";

const ROLE_HELP: Record<Role, string> = {
  viewer: "Read inventory, plans and the audit log",
  analyst: "Run discovery, assessments and plans; use the assistant",
  "connection-admin": "Manage cloud connections and credentials",
  approver: "Approve plans (never their own)",
  operator: "Start approved executions (future release)",
  admin: "Manage members and workspace settings",
};

export function Members() {
  const api = useApi();
  const qc = useQueryClient();
  const { workspaceId, can } = useWorkspace();
  const isAdmin = can("admin");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [error, setError] = useState<string | null>(null);

  const members = useQuery({ queryKey: ["members", workspaceId], queryFn: () => api.members(workspaceId) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["members", workspaceId] });
  const upsert = useMutation({
    mutationFn: (v: { email: string; role: Role }) => api.setMember(workspaceId, v.email, v.role),
    onSuccess: () => {
      setEmail("");
      void refresh();
    },
    onError: (e) => setError(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (userId: string) => api.removeMember(workspaceId, userId),
    onSuccess: refresh,
    onError: (e) => setError(errorMessage(e)),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    upsert.mutate({ email: email.trim(), role });
  };

  return (
    <>
      <PageHeader title="Members" subtitle="Roles are per workspace. Users appear here after an administrator grants access; they must sign in once first." />
      <ErrorBanner error={error ?? (members.error ? errorMessage(members.error) : null)} onDismiss={() => setError(null)} />
      {isAdmin && (
        <Card title="Grant access" className="mb-6">
          <form onSubmit={submit} className="grid items-end gap-3 sm:grid-cols-[1fr_14rem_auto]">
            <Field label="Email">
              <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="engineer@company.com" />
            </Field>
            <Field label="Role">
              <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </Select>
            </Field>
            <Button type="submit" variant="primary" busy={upsert.isPending}>
              Grant
            </Button>
          </form>
          <p className="mt-2 text-xs text-[var(--muted)]">{ROLE_HELP[role]}</p>
        </Card>
      )}
      <Card>
        {members.isLoading ? (
          <Spinner />
        ) : (
          <Table head={["User", "Email", "Role", ""]}>
            {(members.data ?? []).map((m) => (
              <tr key={m.user_id} className="border-b border-[var(--border)]">
                <td className="px-3 py-2">{m.display_name ?? "—"}</td>
                <td className="px-3 py-2 text-[var(--muted)]">{m.email}</td>
                <td className="px-3 py-2">
                  {isAdmin ? (
                    <Select
                      aria-label={`Role for ${m.email}`}
                      className="w-44"
                      value={m.role}
                      onChange={(e) => m.email && upsert.mutate({ email: m.email, role: e.target.value as Role })}
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </Select>
                  ) : (
                    m.role
                  )}
                </td>
                <td className="px-3 py-2 text-right">
                  {isAdmin && (
                    <Button
                      variant="ghost"
                      aria-label={`Remove ${m.email}`}
                      onClick={() => window.confirm(`Remove ${m.email} from this workspace?`) && remove.mutate(m.user_id)}
                    >
                      <Trash2 className="size-4 text-[var(--err)]" />
                    </Button>
                  )}
                </td>
              </tr>
            ))}
            {members.data?.length === 0 && (
              <tr>
                <td colSpan={4} className="px-3 py-6 text-center text-sm text-[var(--muted)]">
                  No explicit members. Platform administrators have access to every workspace.
                </td>
              </tr>
            )}
          </Table>
        )}
      </Card>
    </>
  );
}
