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
