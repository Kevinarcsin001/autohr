# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository State

This is a **greenfield project** (`autohr`). No source code, build system, package manifests, or tests exist yet. The only tracked content is the `.spec-workflow/` scaffolding (spec-workflow MCP tool templates and empty `specs/`, `steering/`, `approvals/`, `archive/` directories).

When asked to implement features, expect to also bootstrap the project's language/tooling — confirm choices with the user before introducing a stack.

## Intended Workflow: Spec-Driven Development

This repo is set up for the **spec-workflow** workflow (Requirements → Design → Tasks → Implementation). The `mcp__spec-workflow__*` tools are first-class here:

1. **Steering docs** (`.spec-workflow/steering/`) — `product.md`, `tech.md`, `structure.md` define project-wide context. None exist yet; create them via `mcp__spec-workflow__steering-guide` when the user wants to establish direction.
2. **Specs** (`.spec-workflow/specs/<name>/`) — Per-feature folders containing `requirements.md`, `design.md`, `tasks.md`. Drive these through `mcp__spec-workflow__spec-workflow-guide` → `spec-status` → `log-implementation`.
3. **Approvals** (`.spec-workflow/approvals/`) — Documents requiring user sign-off flow through `mcp__spec-workflow__approvals`. Do not treat a spec as approved without checking approval status.
4. **Templates** — Default templates in `.spec-workflow/templates/`; users can override per-name in `.spec-workflow/user-templates/` (loaded with priority over defaults).

## Conventions

- **Do not** invent commands (build/lint/test) — none exist. Ask the user how they want the stack set up before writing any code or `package.json`/`Cargo.toml`/etc.
- Prefer the spec-workflow MCP tools over ad-hoc markdown when creating specs, designs, or task lists — they maintain the approval and logging state the user relies on.
- Before creating a spec, check `.spec-workflow/specs/` for existing ones and `mcp__spec-workflow__spec-status` for in-progress work.
