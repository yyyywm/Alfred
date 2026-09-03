> ## Documentation Index
> Fetch the complete documentation index at: https://developers.notion.com/llms.txt
> Use this file to discover all available pages before exploring further.

# API requests

> Make Notion API requests from the terminal.

Use `ntn api` to make authenticated Notion API requests from your terminal. It is useful when you want to inspect an endpoint, test a request body, script an API call, or debug a response without setting up a separate HTTP client.

`ntn api` adds the `Authorization` and `Notion-Version` headers for you. It uses your [CLI authentication](/cli/get-started/authentication) by default, or a token from `NOTION_API_TOKEN` when you set one.

## Make a request

Pass a Notion API path after `ntn api`. The leading slash is optional:

```bash theme={null}
ntn api v1/pages/$PAGE_ID
ntn api /v1/pages/$PAGE_ID
```

Without request body input, `ntn api` sends a `GET` request.

To send a request body, add inline body fields:

```bash theme={null}
ntn api v1/pages \
  parent[page_id]="$PARENT_PAGE_ID" \
  properties[Name][title][0][text][content]="CLI-created page"
```

When body fields are present, `ntn api` sends a `POST` request unless you override the method.

Use `-X` when the endpoint needs a different method:

```bash theme={null}
ntn api "v1/pages/$PAGE_ID" -X PATCH archived:=true
```

## Build request data inline

Inline inputs after the path can set body fields, query parameters, and request headers.

| Form           | Meaning                        | Example                   |
| :------------- | :----------------------------- | :------------------------ |
| `path=value`   | Body field with a string value | `parent[page_id]=abc123`  |
| `path:=json`   | Body field parsed as JSON      | `archived:=true`          |
| `name==value`  | Query parameter                | `page_size==100`          |
| `Header:Value` | Request header                 | `Accept:application/json` |

Use `=` when the value should be a string:

```bash theme={null}
ntn api v1/search query=roadmap
```

Use `:=` when the value should keep its JSON type:

```bash theme={null}
ntn api "v1/pages/$PAGE_ID" -X PATCH \
  archived:=false \
  properties[Priority][number]:=2
```

Typed values can be booleans, numbers, strings, arrays, objects, or `null`:

```bash theme={null}
ntn api v1/search \
  filter:='{"property":"object","value":"page"}' \
  page_size:=10
```

## Choose body syntax

For nested objects, use bracket or dot notation:

```bash theme={null}
ntn api v1/pages \
  parent[page_id]="$PARENT_PAGE_ID" \
  properties.Name.title[0].text.content="Meeting notes"
```

Use explicit array indexes when order matters:

```bash theme={null}
ntn api "v1/blocks/$PAGE_ID/children" -X PATCH \
  children[0][type]=paragraph \
  children[0][paragraph][rich_text][0][text][content]="First paragraph" \
  children[1][type]=heading_2 \
  children[1][heading_2][rich_text][0][text][content]="Next section"
```

Use `[]` to append repeated values in input order:

```bash theme={null}
ntn api v1/comments \
  parent[page_id]="$PAGE_ID" \
  rich_text[][text][content]="First comment line" \
  rich_text[][text][content]="Second comment line"
```

Bracket notation is safest for keys that contain punctuation or spaces:

```bash theme={null}
ntn api "v1/pages/$PAGE_ID" -X PATCH \
  properties[Build version][rich_text][0][text][content]="2026.05.11"
```

<Note>
  Inline request syntax is inspired by [HTTPie](https://github.com/httpie/cli)
  and implemented in [httpcliparser](https://github.com/jclem/httpcliparser).
</Note>

## Send JSON from a file or stdin

Use inline inputs for small bodies. Use stdin or `--data` when the body is easier to write as JSON.

Send a JSON file:

```bash theme={null}
ntn api v1/pages < create-page.json
```

Pipe generated JSON:

```bash theme={null}
jq -n --arg page_id "$PARENT_PAGE_ID" '{
  parent: { page_id: $page_id },
  properties: {
    title: {
      title: [{ text: { content: "Generated page" } }]
    }
  }
}' | ntn api v1/pages
```

Pass a JSON string directly:

```bash theme={null}
ntn api v1/search --data '{"query":"roadmap","page_size":10}'
```

Only use one body source per request: stdin JSON, `--data`, or inline body fields. You can still combine headers and query parameters with any one body source.

## Add query parameters and headers

Use `==` for query parameters:

```bash theme={null}
ntn api v1/search query==roadmap page_size==10
```

Use `Header:Value` for request headers:

```bash theme={null}
ntn api v1/users \
  Accept:application/json \
  X-Trace-Id:cli-test-123
```

Repeated query parameters and headers are preserved in the order you pass them.

<Note>
  `ntn api` already sets `Authorization` and `Notion-Version`. You usually do
  not need to pass those headers manually.
</Note>

## Override the API version

By default, `ntn api` fetches the latest supported `Notion-Version`.

Use `--notion-version` for one request:

```bash theme={null}
ntn api v1/users/me --notion-version 2026-03-11
```

Use `NOTION_API_VERSION` for a shell session or script:

```bash theme={null}
export NOTION_API_VERSION=2026-03-11
ntn api v1/users/me
```

## Inspect endpoints before calling them

List the public API surface:

```bash theme={null}
ntn api ls
```

Print it as JSON for scripts:

```bash theme={null}
ntn api ls --json
```

Show the live endpoint help for a path:

```bash theme={null}
ntn api v1/comments --help
```

Print the reduced OpenAPI fragment for an endpoint:

```bash theme={null}
ntn api v1/comments --spec -X POST
```

Print the official markdown reference page for an endpoint:

```bash theme={null}
ntn api v1/comments --docs -X POST
```

If a path supports multiple methods, pass `-X` so `ntn api` knows which operation to inspect.

## Debug a request

Run with `--verbose` to print request and response metadata to stderr:

```bash theme={null}
ntn --verbose api v1/pages/$PAGE_ID
```

Verbose output includes the final method, URL, request headers, JSON request body, response status, response headers, and response body. The `Authorization` request header is redacted by default.

<Warning>
  `--unsafe-verbose` is a hidden debugging flag that disables `Authorization`
  header redaction in verbose logs. It is dangerous because it can display
  your bearer token in command output. Only use it in a controlled local
  environment, and never paste its output into shared logs, tickets, or chat.
</Warning>

## Troubleshooting

| Problem                                           | What to check                                                                                      |
| :------------------------------------------------ | :------------------------------------------------------------------------------------------------- |
| The request used `POST` unexpectedly              | Body input, `--data`, or stdin JSON makes `POST` the default. Use `-X` to override it.             |
| An inline value has the wrong type                | Use `:=` for JSON values like `true`, `10`, `null`, arrays, and objects. Use `=` only for strings. |
| A nested body path is hard to read                | Prefer bracket notation, especially for property names with spaces or punctuation.                 |
| `--spec` or `--docs` says the method is ambiguous | Add `-X GET`, `-X POST`, `-X PATCH`, or the method you want to inspect.                            |
| The body source conflicts                         | Use only one of stdin JSON, `--data`, or inline body fields.                                       |
| You need to inspect a failing request             | Add `--verbose` and check the final method, URL, status, and `x-request-id`.                       |

## Next steps

<CardGroup cols={2}>
  <Card title="File uploads" icon="https://mintcdn.com/notion-demo/yKfkO8UsVZTLLPNp/icons/nds/arrowTrayUp.svg?fit=max&auto=format&n=yKfkO8UsVZTLLPNp&q=85&s=3b991e18c85512f06c2d7214acf94f12" href="/cli/guides/file-uploads" horizontal color="#0076d7" width="20" height="20" data-path="icons/nds/arrowTrayUp.svg">
    Upload local files or import external files into Notion.
  </Card>

  <Card title="API reference" icon="https://mintcdn.com/notion-demo/7WdlNb9IZkRhGCcR/icons/nds/curlyBraces.svg?fit=max&auto=format&n=7WdlNb9IZkRhGCcR&q=85&s=46f7a8b4a34544f9b03002e4ecc35ad5" href="/reference/intro" horizontal color="#0076d7" width="20" height="20" data-path="icons/nds/curlyBraces.svg">
    Browse Notion API endpoints and schemas.
  </Card>
</CardGroup>
