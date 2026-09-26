import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router";
import { Button, Card, EmptyState, ErrorBanner, Field, Input, PageHeader, Spinner } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useApi } from "../lib/context";

const slugify = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63);

export function Home() {
  const api = useApi();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);

  const create = useMutation({
    mutationFn: () => api.createWorkspace(slug, name),
    onSuccess: async (ws) => {
      await qc.invalidateQueries({ queryKey: ["me"] });
      navigate(`/w/${ws.id}`);
    },
  });

  if (me.isLoading) return <Spinner />;
  if (me.error) return <ErrorBanner error={errorMessage(me.error)} />;
  const memberships = me.data?.memberships ?? [];
  if (memberships.length === 1 && !me.data?.is_platform_admin) return <Navigate to={`/w/${memberships[0]!.workspace.id}`} replace />;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  return (
    <>
      <PageHeader title="Workspaces" subtitle="A workspace holds cloud connections, inventory and plans for one team or program." />
      {memberships.length > 0 ? (
        <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {memberships.map((m) => (
            <button
              key={m.workspace.id}
              onClick={() => navigate(`/w/${m.workspace.id}`)}
              className="rounded-lg border border-[var(--border)] bg-[var(--panel)] p-4 text-left hover:border-[var(--accent)]"
            >
              <div className="font-medium">{m.workspace.name}</div>
              <div className="mt-1 text-xs text-[var(--muted)]">
                {m.workspace.slug} · {m.role}
              </div>
            </button>
          ))}
        </div>
      ) : (
        <Card>
          <EmptyState icon={<Building2 className="size-8" />} title="You are not a member of any workspace yet">
            {me.data?.is_platform_admin
              ? "Create the first workspace below."
              : "Ask a workspace administrator to add you. Your account was created when you signed in."}
          </EmptyState>
        </Card>
      )}
      {me.data?.is_platform_admin && (
        <Card title="Create workspace" className="mt-6 max-w-xl">
          <form onSubmit={submit} className="space-y-4">
            <ErrorBanner error={create.error ? errorMessage(create.error) : null} />
            <Field label="Name">
              <Input
                required
                maxLength={255}
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  if (!slugTouched) setSlug(slugify(e.target.value));
                }}
                placeholder="Data-center exit 2027"
              />
            </Field>
            <Field label="Slug" hint="Lowercase letters, digits and dashes. Used in exports and logs.">
              <Input
                required
                pattern="[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                value={slug}
                onChange={(e) => {
                  setSlugTouched(true);
                  setSlug(e.target.value);
                }}
              />
            </Field>
            <Button type="submit" variant="primary" busy={create.isPending}>
              Create workspace
            </Button>
          </form>
        </Card>
      )}
    </>
  );
}
