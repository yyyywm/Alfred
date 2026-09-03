> ## Documentation Index
> Fetch the complete documentation index at: https://developers.notion.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Installation

> Install the Notion CLI on your machine.

## Install via script (recommended)

The recommended way to install `ntn` on macOS and Linux:

```bash theme={null}
curl -fsSL https://ntn.dev | bash
```

To inspect the installer before running it, view the [install script](https://ntn.dev/install.sh).

To update:

```bash theme={null}
ntn update
```

## Install via npm

Use macOS, Linux, or Windows to install:

```bash theme={null}
npm install --global ntn
```

To update:

```bash theme={null}
npm update --global ntn
```

<Note>
  Requires Node.js 22+ and npm 10+.
</Note>

## Install via Winget (Windows)

In PowerShell or Command Prompt, including either shell in Windows Terminal:

```powershell theme={null}
winget install Notion.ntn
```

You can also confirm the WinGet package:

```powershell theme={null}
winget list --exact --id Notion.ntn
```

Restart your terminal to verify.

To update:

```powershell theme={null}
winget upgrade Notion.ntn
```

<Note>
  We currently only support Windows x64 (x86-64/AMD64)
</Note>

## Verify installation

```bash theme={null}
ntn --version
```

## Shell completions

Enable tab completions for your shell:

```bash theme={null}
ntn completions bash  # or fish, zsh, powershell, elvish
```

## Next steps

<CardGroup cols={2}>
  <Card title="Authentication" icon="lock" href="/cli/get-started/authentication">
    Log in to your Notion workspace.
  </Card>

  <Card title="Workers quickstart" icon="rocket" href="/workers/get-started/quickstart">
    Create and deploy your first Notion Worker.
  </Card>
</CardGroup>
