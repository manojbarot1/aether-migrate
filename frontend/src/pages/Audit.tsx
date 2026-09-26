import { useInfiniteQuery } from "@tanstack/react-query";
import { ScrollText } from "lucide-react";
import { Fragment, useState } from "react";
import { Button, Card, Code, EmptyState, ErrorBanner, Input, PageHeader, Spinner, StatusBadge, Table } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useApi, useWorkspace } from "../lib/context";

export function Audit() {
  const api = useApi();
  const { workspaceId } = useWorkspace();
  const [filter, setFilter] = useState("");
  const [applied, setApplied] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const q = useInfiniteQuery({
    queryKey: ["audit", workspaceId, applied],
    queryFn: ({ pageParam }) => api.audit(workspaceId, { before: pageParam, action: applied || undefined, limit: 50 }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.next_before ?? undefined,
  });
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <PageHeader
        title="Audit log"
        subtitle="Append-only and hash-chained: every change, connection test and cloud API call is recorded and tamper-evident."
      />
      <ErrorBanner error={q.error ? errorMessage(q.error) : null} />
      <Card
        title={
          <form
            className="flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setApplied(filter.trim());
            }}
          >
            <Input className="w-64" placeholder="Filter by action prefix, e.g. connection." value={filter} onChange={(e) => setFilter(e.target.value)} />
            <Button type="submit">Filter</Button>
          </form>
        }
      >
        {q.isLoading ? (
          <Spinner />
        ) : items.length === 0 ? (
          <EmptyState icon={<ScrollText className="size-8" />} title="No events" />
        ) : (
          <>
            <Table head={["#", "Time", "Action", "Actor", "Target", "Result"]}>
              {items.map((e) => (
                <Fragment key={e.id}>
                  <tr
                    className="cursor-pointer border-b border-[var(--border)] hover:bg-[var(--panel-2)]"
                    onClick={() => setOpen(open === e.id ? null : e.id)}
                  >
                    <td className="px-3 py-2 font-mono text-xs text-[var(--muted)] tabular-nums">{e.seq}</td>
                    <td className="px-3 py-2 whitespace-nowrap text-[var(--muted)]">{new Date(e.occurred_at).toLocaleString()}</td>
                    <td className="px-3 py-2 font-mono text-xs">{e.action}</td>
                    <td className="px-3 py-2">{e.actor_display ?? e.actor_type}</td>
                    <td className="px-3 py-2 text-[var(--muted)]">{e.target_type ?? "—"}</td>
                    <td className="px-3 py-2">
                      <StatusBadge status={e.status} />
                    </td>
                  </tr>
                  {open === e.id && (
                    <tr className="border-b border-[var(--border)] bg-[var(--panel-2)]">
                      <td colSpan={6} className="px-4 py-3">
                        <div className="mb-2 grid gap-1 text-xs text-[var(--muted)] sm:grid-cols-2">
                          <div>Event ID: <span className="font-mono">{e.id}</span></div>
                          <div>Request ID: <span className="font-mono">{e.request_id ?? "—"}</span></div>
                          <div>Target ID: <span className="font-mono">{e.target_id ?? "—"}</span></div>
                          <div className="truncate">Hash: <span className="font-mono">{e.hash}</span></div>
                        </div>
                        <Code>{JSON.stringify(e.details, null, 2)}</Code>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </Table>
            {q.hasNextPage && (
              <div className="mt-3 flex justify-center">
                <Button busy={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
                  Load older events
                </Button>
              </div>
            )}
          </>
        )}
      </Card>
    </>
  );
}
