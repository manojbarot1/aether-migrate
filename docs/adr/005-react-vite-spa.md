# ADR-005: React + Vite SPA (No Next.js)

**Status:** Accepted  
**Date:** 2025-01-01

## Context

The frontend is a data-dense operations console (inventory tables, topology graphs, assessment dashboards). SSR and SEO are not requirements. Options considered: Next.js, Remix, plain React + Vite.

## Decision

Use a **React 19 + Vite** single-page application. TanStack Router provides type-safe client-side routing. TanStack Query handles server-state with caching and background refresh. shadcn/ui provides accessible component primitives built on Radix UI.

Next.js is rejected because SSR adds deployment complexity (Node.js server) for no benefit in this auth-gated application. All routes require authentication, so server rendering provides no SEO value.

The built SPA is served as static files by the UBI9 nginx-122 container behind Caddy.

## Consequences

- **+** Simple deployment: Vite build → static files → nginx.
- **+** No server-side state to manage; all data flows through the REST API.
- **+** TanStack Router provides full type safety for route params and search params.
- **−** Full page load always requires JavaScript; not accessible to screen readers without ARIA work.
- **−** No incremental static regeneration or server components.
