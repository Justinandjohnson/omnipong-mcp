# Publishing omnipong to the official MCP Registry

Publishes the metadata in `server.json` (a **remote**, streamable-http entry — no
package artifact) to `registry.modelcontextprotocol.io`, under the GitHub-verified
namespace `io.github.Justinandjohnson/*`.

## Prerequisite — fill in the real hosted URL

`server.json` ships a placeholder: `https://REPLACE-WITH-DEPLOY-HOST/mcp`.
**Deploy the server first, then replace that with the real, auth-protected
`/mcp` URL.** Do not publish the placeholder. (Per ARCHITECTURE.md, `--http`
must not go public until auth + rate-limiting are in place.)

## Step 1 — Install the `mcp-publisher` CLI

Homebrew:

    brew install mcp-publisher

Or a pre-built binary (macOS/Linux):

    curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" | tar xz mcp-publisher && sudo mv mcp-publisher /usr/local/bin/

## Step 2 — Validate (optional but recommended)

    cd /Users/jjohnson/codesd/omnipong-mcp
    mcp-publisher validate

## Step 3 — Log in via GitHub OAuth

Authorizes the `io.github.Justinandjohnson` namespace. Opens a browser flow:

    mcp-publisher login github

## Step 4 — Publish

Reads `server.json` from the current directory and uploads it:

    mcp-publisher publish

## Step 5 — Verify it is live

    curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.Justinandjohnson/omnipong"
