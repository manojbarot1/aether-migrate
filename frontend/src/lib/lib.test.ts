import { describe, expect, it } from "vitest";
import { relativeTime } from "../components/ui";
import { ApiError, errorMessage } from "./api";
import { roleAtLeast } from "./types";

describe("roleAtLeast", () => {
  it("orders roles from viewer to admin", () => {
    expect(roleAtLeast("admin", "viewer")).toBe(true);
    expect(roleAtLeast("connection-admin", "analyst")).toBe(true);
    expect(roleAtLeast("analyst", "connection-admin")).toBe(false);
    expect(roleAtLeast(undefined, "viewer")).toBe(false);
  });
});

describe("relativeTime", () => {
  it("handles missing and recent timestamps", () => {
    expect(relativeTime(null)).toBe("never");
    expect(relativeTime(new Date().toISOString())).toBe("just now");
    expect(relativeTime(new Date(Date.now() - 5 * 60_000).toISOString())).toBe("5 min ago");
  });
});

describe("errorMessage", () => {
  it("includes a short request id for support", () => {
    expect(errorMessage(new ApiError(403, "forbidden", "requires role 'admin'", "0123456789abcdef"))).toBe(
      "requires role 'admin' (request 01234567)",
    );
    expect(errorMessage(new Error("boom"))).toBe("boom");
  });
});

describe("readEventStream", () => {
  it("parses events split across arbitrary chunks and ignores keep-alives", async () => {
    const { readEventStream } = await import("./api");
    const raw = 'event: start\ndata: {"conversation_id":"c"}\n\n: keep-alive\n\nevent: text\ndata: {"delta":"Hel"}\n\nevent: text\ndata: {"delta":"lo"}\n\nevent: done\ndata: {"usage":{"input_tokens":1,"output_tokens":2},"steps":1}\n\n';
    const bytes = new TextEncoder().encode(raw);
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        for (let i = 0; i < bytes.length; i += 7) c.enqueue(bytes.slice(i, i + 7));
        c.close();
      },
    });
    const seen: string[] = [];
    await readEventStream(body, (e) => seen.push(e.event === "text" ? `text:${e.data.delta}` : e.event));
    expect(seen).toEqual(["start", "text:Hel", "text:lo", "done"]);
  });
});
