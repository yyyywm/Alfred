> ## Documentation Index
> Fetch the complete documentation index at: https://developers.notion.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Authentication

> Log in to your Notion workspace and manage CLI credentials.

## Log in

Authenticate with your Notion workspace:

```bash theme={null}
ntn login
```

This opens your browser to an authorization page. Confirm that the code in the browser matches the code printed in your terminal before approving. This prevents another page from completing the login in your name.

Your workspace-scoped token will be stored securely in your system's keychain.

If you've already logged in to one or more workspaces, you can pick existing workspace to switch the default, or pick **Authenticate with new workspace** to start a fresh browser flow and add another workspace.

<Note>
  `ntn login` requires full workspace membership. [Guests](https://www.notion.com/help/whos-who-in-a-workspace) and [restricted members](https://www.notion.com/help/whos-who-in-a-workspace) cannot log in with the Notion CLI. If you need CLI access, ask a workspace admin to upgrade your role. See [Personal access tokens](/guides/get-started/personal-access-tokens) for more on who can create tokens.
</Note>

## Log in without a browser

On a remote machine, container, or CI runner that can't open a browser, use `--no-browser` to get a two-step login flow:

1. Run `ntn login --no-browser`. It prints a URL, a verification code, and a `ntn login poll` command.
2. Open the URL in any browser, sign in, and confirm the verification code.
3. Run `ntn login poll` on the original machine to redeem the token.

`ntn login` also falls back to this flow automatically when it detects there is no terminal (e.g. piped input).

Login sessions expire after a short window. If polling fails because the session expired, run `ntn login` again to start over.

For unattended use (CI, scripts, bots), prefer a [personal access token](#use-a-personal-access-token) instead.

## Target a specific workspace

To run a single command against a non-default workspace without switching defaults, set `NOTION_WORKSPACE_ID`:

```bash theme={null}
NOTION_WORKSPACE_ID=<workspace-id> ntn api v1/users/me
```

Workspace IDs are listed in the output of `ntn debug`.

## Use a personal access token

For unattended use, authenticate with a [personal access token](/guides/get-started/personal-access-tokens) (PAT) by exporting it as `NOTION_API_TOKEN`:

```bash theme={null}
export NOTION_API_TOKEN=ntn_xxx...
ntn api v1/users/me
```

`NOTION_API_TOKEN` takes precedence over anything stored in the keychain, so the same shell can mix `ntn login`-based commands and PAT-based commands depending on what's exported.

## Inspect your session

```bash theme={null}
ntn doctor
```

## Log out

```bash theme={null}
ntn logout
```

This forgets every cached workspace, deletes each one's token from the keychain, and clears the default workspace. The `config.json` and `workspaces.json` files themselves stay in place — run `ntn login` to repopulate them.

## Where credentials are stored

Tokens live in your OS credential store (Keychain on macOS, Secret Service on Linux) under the service name `notion-cli`, with the workspace ID as the account.

Two files sit alongside them in the CLI config directory:

* `config.json` — CLI version, default workspace per, and the optional `keyring` toggle.
* `workspaces.json` — cached workspace IDs and names for the interactive picker.

The config directory is `NOTION_HOME` if set, otherwise `$XDG_CONFIG_HOME/notion`, `$HOME/.config/notion`, or `$HOME/.notion` as fallbacks.

### Opt out of the OS keychain

On systems without a usable keychain, `ntn login` fails with a keychain error. Common examples include Docker containers, CI runners, SSH sessions to a Linux server, etc.

Set `NOTION_KEYRING=0` to store tokens in plain JSON at `auth.json` in the config directory instead. Treat that file like any other secret.

```bash theme={null}
NOTION_KEYRING=0 ntn login
```

To make it permanent, set `"keyring": false` in `config.json`. The env var always wins.

## Environment variables

| Variable              | Purpose                                                                                              |
| :-------------------- | :--------------------------------------------------------------------------------------------------- |
| `NOTION_API_TOKEN`    | When this is set, it'll take precedence over `ntn login`'s keychain entry. Handy for scripts and CI. |
| `NOTION_WORKSPACE_ID` | Override the default workspace for a single command.                                                 |
| `NOTION_KEYRING`      | Set to `0` to use file-based storage instead of the OS keychain.                                     |
| `NOTION_HOME`         | Override the config directory.                                                                       |
| `NOTION_ENV`          | Same as `--env`. Rarely needed.                                                                      |

Run `ntn login --help` for the full list.

## Next steps

<CardGroup cols={2}>
  <Card title="Workers quickstart" icon="rocket" href="/workers/get-started/quickstart">
    Create and deploy your first Notion Worker.
  </Card>

  <Card title="API requests" icon="terminal" href="/cli/guides/api-requests">
    Make Notion API requests from the terminal.
  </Card>

  <Card title="Command reference" icon="book-open" href="/cli/reference/commands">
    Full reference for every ntn command.
  </Card>

  <Card title="Personal access tokens" icon="key" href="/guides/get-started/personal-access-tokens">
    Create tokens for scripts and CI.
  </Card>
</CardGroup>
