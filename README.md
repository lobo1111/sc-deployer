## `scd` (Service Catalog Deployer)

`scd` is a single Rust binary that:

- Creates a new project directory with **git initialized**
- Stores all config/state in **`.deployer/`**
- Treats local YAML as **desired state** and can **sync** portfolios/products to AWS at any time
- Can **destroy** all managed resources safely

### Project layout

`scd init --name <project>` creates:

- `.deployer/`
  - `profiles.yaml`
  - `bootstrap.yaml`
  - `catalog.yaml`
  - `.bootstrap-state.json` (ignored by git)
  - `.deploy-state.json` (ignored by git)
- `products/<product>/`
  - `product.yaml`
  - `template.yaml`
- `.gitignore`
- `.git/` (via `git init`)

### CLI (high level)

- `scd init --name <project> [--sample] [--interactive]`
- `scd project-status`
- `scd connect -e <env> [--aws-profile <p>] [--region <r>] [--account-id <id>] [--sso-login]`
- `scd sync -e <env> [--dry-run]`
- `scd profiles list`
- `scd profiles set -e <env> --aws-profile <p> --region <r> --account-id <id> [--verify] [--sso-login]`
- `scd profiles whoami -e <env>`
- `scd products list`
- `scd products add --name <product> [--path <dir>] [--portfolio <portfolio>] [--description <text>] [--dependency <p>...] [--output <o>...] [--param-mapping Param=dep.out...]`
- `scd products graph`
- `scd completion <bash|zsh|fish|powershell>`
- `scd deploy validate -e <env>`
- `scd deploy plan -e <env> [-p <product>...]`
- `scd deploy publish -e <env> [-p <product>...] [--dry-run] [--force]`
- `scd deploy apply -e <env> [-p <product>...] [--dry-run]`
- `scd deploy status -e <env>`
- `scd deploy terminate -e <env> [-p <product>...] [--dry-run] [--force]`
- `scd destroy -e <env> [--dry-run] [--force]`

### Shell autocompletion

Generate completion for your shell:

```bash
scd completion bash > /tmp/scd.bash
```

Typical installs:
- bash: `scd completion bash > /etc/bash_completion.d/scd`
- zsh: `scd completion zsh > "${fpath[1]}/_scd"`

### Build (static Linux x86_64)

```bash
rustup target add x86_64-unknown-linux-musl
cargo build --release --target x86_64-unknown-linux-musl
./target/x86_64-unknown-linux-musl/release/scd --help
```

### Cursor MCP (run `scd` inside Cursor)

This repo includes a **Rust MCP server** (`scd-mcp`) so Cursor can call `scd` as tools.

Build it once:

```bash
cargo build -p scd-mcp
```

Then ensure `.cursor/mcp.json` exists (already included in this repo) and restart Cursor.

