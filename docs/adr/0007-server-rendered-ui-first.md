# 0007: Server-rendered UI first, React when interactivity needs it

- Status: accepted
- Date: 2026-11-01

## Context

The plan (02_ARCHITECTURE.md) called for a React + TypeScript + Vite UI. M1 needs two pages: an incident list and an incident detail page. The development session can't reach the npm registry, so React code couldn't be built, run or tested before CI, and there would be no committed lockfile. Self-hosting simplicity also matters: fewer moving parts in the image is better.

## Options considered

1. **React SPA now**: matches the original plan and scales to rich interactions, but needs a Node build stage, an unlockable dependency tree from this environment, and duplicate data models in TypeScript, for two mostly static pages.
2. **Server-rendered HTML** (Jinja2 templates from FastAPI): no build step, no client bundle, works without JavaScript, auto-escaped templates, the same query layer as the JSON API.
3. **htmx-style partial updates on top of option 2**: adds interactivity later without a SPA.

## Decision

Option 2 for M1. The JSON API (`/v1/incidents`) is built alongside, with the same queries, so a React UI can be added later without backend changes. Revisit in M3, when the incident timeline and evidence panel need interaction: options 1 or 3 then, depending on how interactive it really is and whether registry access is available.

## Consequences

- One less toolchain in the repo and the image; UI changes are tested with the same pytest setup (including an XSS escaping test).
- Pages refresh by reload; the incident page auto-refreshes only while a summary is pending.
- Moving to React later means rewriting templates, not the API. The cost of this choice is small and contained.
