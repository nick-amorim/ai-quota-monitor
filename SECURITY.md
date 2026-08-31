# Security Policy

QuotaPilot is planned as a self-hosted application that stores local runtime state and Codex authentication homes on the deployment host.

## Supported Versions

The project is pre-implementation. Supported versions will be listed after the first release.

## Reporting Security Issues

Open a private security advisory on GitHub when available. If advisories are not enabled, contact the repository owner before publishing details.

## Secret Handling Rules

- Do not commit `.env` files, SQLite databases, runtime logs, Codex homes, account workspaces, OAuth material, or browser/session exports.
- Do not add OpenAI API keys. V1 is explicitly designed around official Codex authentication, not API-key access.
- Do not scrape ChatGPT or call undocumented ChatGPT backend endpoints.
- Account A and Account B must use isolated `CODEX_HOME` directories.
