# CLAUDE.md - EducaChile Studio

## Project context

This repository belongs to EducaChile Studio, a platform that converts PowerPoint presentations into educational videos using AI-generated voice, talking avatars, slide rendering, subtitles, and final video composition.

## Knowledge base

The project knowledge base is maintained in Obsidian at:

`/Users/benjamin/Documents/Obsidian/EducaChile Studio KB`

Claude must use this knowledge base as the source of truth for project context, architecture, flows, integrations, solved problems, prompts, roadmap, UI/UX decisions, deployment notes, and technical decisions.

## Critical completion rule

When code is modified, the task is not complete until the Obsidian knowledge base has been reviewed and updated if the change affects the project context.

Claude must not finish a coding task without checking whether the KB needs an update.

## Mandatory documentation rule

Claude must update the Obsidian knowledge base after any significant change.

A significant change includes:

- New feature
- Bug fix
- Architecture change
- API integration change
- Database migration
- Worker/pipeline change
- Deployment change
- Important UI/UX change
- API credentials or settings change
- New error discovered
- New technical decision
- Change that affects future development
- Change that affects the user flow

## What to update

Depending on the change, update one or more of these sections:

- `00 - Inicio/Mapa del proyecto.md`
- `01 - Arquitectura/`
- `02 - Flujos principales/`
- `03 - Integraciones IA/`
- `04 - Problemas y soluciones/`
- `05 - Prompts Codex/`
- `06 - Roadmap/`
- `07 - Decisiones técnicas/`
- `08 - Deploy y producción/`
- `09 - UI UX/`
- `10 - Bitácora/`

Always add a new entry in:

`10 - Bitácora/Bitácora de cambios.md`

## Documentation format

Every documentation update should include:

- Date
- Summary of the change
- Files modified
- Reason for the change
- Impact on architecture, flow, or UX
- Errors found, if any
- How it was solved
- Pending tasks, if any

## Before coding

Before making a major change:

1. Read the relevant files in the Obsidian KB.
2. Understand the existing architecture and current flows.
3. Identify which part of the system is affected.
4. Avoid changing working logic unless necessary.
5. Preserve the current avatar/video generation flow unless the task explicitly requires changing it.

## During coding

While modifying the project:

1. Prefer small, focused changes.
2. Reuse existing logic before creating new logic.
3. Avoid duplicating credential, video generation, or storage logic.
4. Keep UI labels in Spanish when working on user-facing screens.
5. Do not expose raw API keys or secrets in the frontend.
6. Keep backend-side credential handling secure.

## After coding

After making a major change:

1. Run the relevant validation, tests, linting, or build checks when available.
2. If validation cannot be run, explain why.
3. Update the Obsidian KB.
4. Add a new entry in `10 - Bitácora/Bitácora de cambios.md`.
5. Update related notes if the change affects architecture, flows, errors, integrations, UI/UX, deployment, or roadmap.

## Final response format

At the end of each coding task, Claude must summarize:

- Code changes made
- Files modified
- Validation performed
- Obsidian KB files updated
- Pending tasks or risks

## Important project constraints

- Do not break the current working avatar generation logic.
- Keep the video generation pipeline stable.
- Preserve Spanish UI labels where requested.
- Prefer concise prompts and concise documentation.
- Document major bugs and their fixes.
- Keep API credentials secure and never expose raw saved keys in frontend responses.
- Prefer reusing existing endpoints and services before creating new ones.