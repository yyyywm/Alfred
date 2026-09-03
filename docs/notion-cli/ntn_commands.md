> ## Documentation Index
> Fetch the complete documentation index at: https://developers.notion.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Command reference

> Complete reference for all ntn commands.

## Global flags

Available on every command.

| Flag                           | Description                                                                                                                                                                          | Example                                                        |
| :----------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------- |
| `-v, --verbose`                | Show full error details including source chains.                                                                                                                                     | `ntn workers deploy --verbose`                                 |
| `--workers-config-file <path>` | Path to a `workers.json` config file (overrides the default lookup in the current directory). The `workspaceId` field in this file selects the workspace for authenticated commands. | `ntn workers deploy --workers-config-file ./prod.workers.json` |
| `-V, --version`                | Print version.                                                                                                                                                                       | `ntn --version`                                                |
| `-h, --help`                   | Print help.                                                                                                                                                                          | `ntn workers --help`                                           |

## Environment variables

| Variable                     | Description                                                                                  |
| :--------------------------- | :------------------------------------------------------------------------------------------- |
| `NOTION_API_TOKEN`           | API token for authentication (overrides keychain).                                           |
| `NOTION_KEYRING`             | Set to `0` to use file-based auth (`~/.config/notion/auth.json`) instead of the OS keychain. |
| `NOTION_WORKERS_CONFIG_FILE` | Path to `workers.json` (same as `--workers-config-file`).                                    |
| `NOTION_WORKSPACE_ID`        | Workspace ID to operate on; skips the workspace prompt.                                      |

## Authentication

| Command      | Description                                         | Example      |
| :----------- | :-------------------------------------------------- | :----------- |
| `ntn login`  | Log in to Notion and connect to a workspace.        | `ntn login`  |
| `ntn logout` | Clear stored credentials for the current workspace. | `ntn logout` |

## Workers

Commands for managing Notion Workers. Most commands that target a specific worker resolve the worker ID in this order:

1. The `--worker-id` flag (or positional `<worker-id>` argument).
2. The `workerId` field in `workers.json` in the current directory.

If neither is set, the command exits with an error.

### Common flags

Supported across most `workers` subcommands.

| Flag               | Description                                                                       |
| :----------------- | :-------------------------------------------------------------------------------- |
| `--json`           | Output as JSON. Mutually exclusive with `--plain`.                                |
| `--plain`          | Output as tab-separated values with no headers. Mutually exclusive with `--json`. |
| `--worker-id <id>` | Target a specific worker. Defaults to `workers.json`.                             |

### Commands

| Command                          | Description                                                                                                                                                                                                                                                                                                                               |
| :------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ntn workers new [directory]`    | Scaffold a new worker project. Prompts for a worker name when `stdin` is a TTY. Flags: `--force` (overwrite conflicting files), `--git`/`--no-git` (force or skip `git init`), `--install`/`--no-install` (force or skip dependency installation).                                                                                        |
| `ntn workers deploy`             | Build and upload the worker in the current directory. Creates a new worker if `workers.json` is missing; otherwise updates the existing worker. Flags: `--name <name>` (required when creating, forbidden when updating), `--local-build` (build locally instead of in the cloud), `--no-git` (walk the filesystem instead of using git). |
| `ntn workers list`               | List all workers in the active workspace. Alias: `ls`. No flags beyond common.                                                                                                                                                                                                                                                            |
| `ntn workers get [worker-id]`    | Show details for a single worker. No flags beyond common.                                                                                                                                                                                                                                                                                 |
| `ntn workers create`             | Create a worker without deploying any code. Flags: `--name <name>`.                                                                                                                                                                                                                                                                       |
| `ntn workers delete [worker-id]` | Delete a worker. Alias: `rm`. Flags: `--yes` (skip confirmation prompt).                                                                                                                                                                                                                                                                  |
| `ntn workers exec <key>`         | Run a capability (sync, tool, or webhook) and print its output. Flags: `-d/--data <json>` (JSON input; reads stdin if omitted), `--stream` (stream output as produced), `-l/--local` (run locally via `tsx`), `--dotenv <path>` (env file for `--local`, default `.env`), `--no-dotenv` (skip loading `.env`).                            |
| `ntn workers capabilities list`  | List all deployed capabilities for a worker. Alias: `ls`. No flags beyond common.                                                                                                                                                                                                                                                         |
| `ntn workers tui`                | Open the interactive terminal UI for managing workers. Alias: `ui`. No flags.                                                                                                                                                                                                                                                             |

## Workers — sync

Manage scheduled syncable capabilities. Each subcommand takes a `<key>` identifying the capability. See the [Syncs guide](/workers/guides/syncs) for usage patterns, pagination, and scheduling.

The [Workers common flags](#common-flags) (`--json`, `--plain`, `--worker-id`) apply to every `sync` subcommand.

| Command                                    | Description                                                                                                                                                                                                                                                                                                                                      |
| :----------------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ntn workers sync status [capability-key]` | Show live-updating status for a worker's syncable capabilities. Filters to a single capability when `capability-key` is provided. Flags: `--no-watch` (print once and exit), `--interval <seconds>` (poll interval in watch mode, default `2`).                                                                                                  |
| `ntn workers sync trigger <key>`           | Trigger a syncable capability to run now, bypassing the schedule. Flags: `--preview` (invoke without writing to the target), `--context <json>` (cursor from a previous `--preview` run's `nextContext`), `-l/--local` (run locally via `tsx`), `--dotenv <path>` (env file for `--local`, default `.env`), `--no-dotenv` (skip loading `.env`). |
| `ntn workers sync pause <key>`             | Pause scheduled execution for a sync. In-flight runs are not interrupted. No flags beyond common.                                                                                                                                                                                                                                                |
| `ntn workers sync resume <key>`            | Resume scheduled execution for a previously paused sync. No flags beyond common.                                                                                                                                                                                                                                                                 |
| `ntn workers sync state get <key>`         | Print the current cursor and stats for a sync. No flags beyond common.                                                                                                                                                                                                                                                                           |
| `ntn workers sync state reset <key>`       | Clear a sync's cursor and stats so it restarts from scratch on the next run. No flags beyond common.                                                                                                                                                                                                                                             |

## Workers — databases

Manage a worker's declared databases and their bindings to existing Notion databases. See [Attach an existing database](/workers/guides/syncs#attach-an-existing-database) for usage and safety guidance — attaching can overwrite or delete rows in the target database, and `attach`/`detach` both prompt for confirmation before making any change.

The [Workers common flags](#common-flags) (`--json`, `--plain`, `--worker-id`) apply to every `databases` subcommand.

| Command                                                  | Description                                                                                                                                                                                                                                                                                                                                                                                                      |
| :------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ntn workers databases attach <database-key> <database>` | Bind an attached database declaration to an existing Notion database. `<database>` is a database link/URL or a data source ID. Adds every declared column, locks declared columns read-only, and takes over rows matching the primary key. In the default replace sync mode, rows the sync doesn't produce are deleted on the next run. Flags: `--yes` (skip the confirmation prompt; requires a TTY otherwise). |
| `ntn workers databases detach [database-key]`            | Release a bound database from sync control. Data and columns are left in place. Exactly one of the positional `database-key` or `--data-source` is required. Flags: `--data-source <id>` (target by data source ID/link instead of declaration key), `--yes` (skip the confirmation prompt; requires a TTY otherwise).                                                                                           |
| `ntn workers databases list [worker-id]`                 | List every database declared in the worker's manifest, its lifecycle type (`managed`/`attached`), and whether it's bound to a Notion database yet. Alias: `ls`. No flags beyond common.                                                                                                                                                                                                                          |

## Workers — env

Manage encrypted environment variables for a worker. Values are write-only; they are never returned by `list`. See the [Secrets guide](/workers/guides/secrets) for usage patterns and local development workflows.

| Command                              | Description                                                                                                                                                                                  |
| :----------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ntn workers env set <KEY=VALUE>...` | Set one or more environment variables.                                                                                                                                                       |
| `ntn workers env list`               | List the keys of all environment variables (values are hidden). Alias: `ls`.                                                                                                                 |
| `ntn workers env unset <key>`        | Remove an environment variable. Aliases: `delete`, `rm`.                                                                                                                                     |
| `ntn workers env pull`               | Download remote environment variables to a local `.env` file. Flags: `--file <path>` (default `.env`), `--no-file` (print to stdout instead of writing), `--yes` (skip confirmation prompt). |
| `ntn workers env push`               | Push a local `.env` file to your worker. Flags: `--file <path>` (default `.env`), `--yes` (skip confirmation prompt).                                                                        |

## Workers — OAuth

Manage OAuth connections for capabilities that authenticate against external providers. See the [OAuth guide](/workers/guides/oauth) for the full setup flow.

| Command                               | Description                                                                                                             |
| :------------------------------------ | :---------------------------------------------------------------------------------------------------------------------- |
| `ntn workers oauth start <key>`       | Start an OAuth flow for an OAuth capability. Opens the provider's authorization URL.                                    |
| `ntn workers oauth token <key>`       | Print an OAuth access token for a capability. Intended for debugging. With `--plain`, prints just the token for piping. |
| `ntn workers oauth show-redirect-url` | Print the OAuth redirect URL to configure with your provider.                                                           |

## Workers — runs

| Command                          | Description                                 |
| :------------------------------- | :------------------------------------------ |
| `ntn workers runs list`          | List recent runs for a worker. Alias: `ls`. |
| `ntn workers runs logs <run-id>` | Print logs for a specific run.              |

## Workers — webhooks

See the [Webhooks guide](/workers/guides/webhooks) for defining webhook handlers, request verification, and retries.

| Command                                 | Description                                                         |
| :-------------------------------------- | :------------------------------------------------------------------ |
| `ntn workers webhooks list [worker-id]` | List webhook URLs for a worker's webhook capabilities. Alias: `ls`. |

## API

| Command          | Description                                                                                             |
| :--------------- | :------------------------------------------------------------------------------------------------------ |
| `ntn api <path>` | Make an authenticated Notion API request. See [API requests](/cli/guides/api-requests) for full syntax. |
| `ntn api ls`     | List all available API endpoints.                                                                       |

## Data sources

### `ntn datasources query <data-source-id>`

Query pages in a data source.

Flags:

* `--limit <n>`: page size, default `25`.
* `--start-cursor <cursor>`: pagination cursor from a previous response.
* `-s, --sort <spec>`: `<property> [asc|desc]`, repeatable.
* `--filter <json>`: raw filter JSON. See [Filter data source entries](/reference/filter-data-source-entries) for the expected shape.
* `--filter-file <path>`: read filter JSON from a file; pass `-` for stdin.
* `--notion-version <version>`: also settable via `NOTION_API_VERSION`.

### `ntn datasources resolve <database-id>`

Resolve a Notion database ID to its data source IDs.

Flags:

* `--notion-version <version>`: also settable via `NOTION_API_VERSION`.

## Pages

| Command                     | Description                                                                                                                                                                                                       |
| :-------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ntn pages trash <page-id>` | Trash a page. Flags: `--yes` (skip confirmation prompt), `--notion-version <version>`.                                                                                                                            |
| `ntn pages get <page-id>`   | Retrieve a page as Markdown. Flags: `--json` (output as JSON), `--notion-version <version>`.                                                                                                                      |
| `ntn pages create`          | Create a page from Markdown content. Flags: `--parent <ref>` (parent target: `page:<id>`, `database:<id>`, or `data-source:<id>`), `--content <markdown>` (reads stdin if omitted), `--notion-version <version>`. |
| `ntn pages edit <page-id>`  | Update a page's content from Markdown. Flags: `--content <markdown>` (reads stdin if omitted), `--allow-deleting-content` (allow deletion of child pages and databases), `--notion-version <version>`.            |

## Files

| Command                     | Description              |
| :-------------------------- | :----------------------- |
| `ntn files create`          | Upload a file to Notion. |
| `ntn files get <upload-id>` | Get upload details.      |
| `ntn files list`            | List file uploads.       |

## Diagnostics

| Command      | Description                                                                                    |
| :----------- | :--------------------------------------------------------------------------------------------- |
| `ntn doctor` | Check the health of your Notion CLI setup. Reports on auth, keychain, network, and config.     |
| `ntn update` | Update `ntn` to the latest version. Flags: `--force` (reinstall even when already up to date). |
