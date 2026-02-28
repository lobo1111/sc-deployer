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
- `scd products test [-p <product>...]` — run unit tests; auto-detects from pyproject.toml (pytest) or package.json (npm test), or use `test_command` in catalog
- `scd completion <bash|zsh|fish|powershell>`
- `scd deploy validate -e <env>`
- `scd deploy plan -e <env> [-p <product>...]`
- `scd deploy publish -e <env> [-p <product>...] [--dry-run] [--force]`
- `scd deploy publish-code -e <env> [-p <product>...] [--dry-run]` — upload Lambda/AppSync code to S3 (code-only changes)
- `scd deploy apply -e <env> [-p <product>...] [--dry-run] [--force]`
- `scd deploy status -e <env>`
- `scd deploy terminate -e <env> [-p <product>...] [--dry-run] [--force]`
- `scd destroy -e <env> [--dry-run] [--force]`

### `profiles.yaml`: product parameter values

You can define **per-environment, per-product provisioning parameter values** in `.deployer/profiles.yaml`:

```yaml
profiles:
  dev:
    aws_profile: sandbox
    aws_region: us-east-1
    account_id: "111111111111"
    product_parameters:
      networking:
        VpcCidr: 10.0.0.0/16
      database:
        DbName: app
```

These values are merged into the parameters sent during `scd deploy apply` (after dependency-based mappings), and `scd` always sets `Environment` automatically.

### Deploy: publish/apply changed-only

By default:

- `scd deploy publish` **skips** products whose `template.yaml` hasn't changed since the last publish (based on a stored template hash). Use `--force` to publish anyway.
- `scd deploy publish-code` uploads `code/` (Lambda zips) and `resolvers/` (AppSync JS) to the template bucket, with content-hash suffixes to force redeploy when code changes. Run `apply --force` after code-only changes.
- `scd deploy apply` **skips** products that have already applied the currently published version. Use `--force` to apply anyway.

### `catalog.yaml`: per-product launch role

Service Catalog uses a **LAUNCH constraint** to decide what IAM role CloudFormation will assume for a product.
By default, `scd sync` creates an environment-wide launch role and uses it for all products.

If you want **scd to create the role** (so it definitely exists before provisioning), define `launch_role` on the product:

```yaml
products:
  database:
    path: database
    portfolio: infra
    launch_role:
      name: DatabaseLaunchRole
      managed_policy_arns:
        - arn:aws:iam::aws:policy/AWSCloudFormationFullAccess
        - arn:aws:iam::aws:policy/AmazonRDSFullAccess
      inline_policies:
        AllowReadParams:
          Version: "2012-10-17"
          Statement:
            - Effect: Allow
              Action:
                - ssm:GetParameter
                - ssm:GetParameters
              Resource: "*"
```

If you already have a role and just want `scd` to use it, set `launch_role_arn` on that product in `.deployer/catalog.yaml`:

```yaml
products:
  database:
    path: database
    portfolio: infra
    launch_role_arn: arn:aws:iam::${account_id}:role/DatabaseLaunchRole
    dependencies: []
    parameter_mapping: {}
    outputs: []
```

How `scd sync` behaves:

- If `launch_role` is set: `scd sync` creates/ensures that IAM role exists, then uses it for the product's LAUNCH constraint.
- Else if `launch_role_arn` is set: `scd sync` uses that existing role ARN for the product's LAUNCH constraint.
- Else: `scd sync` uses the environment default role (`scd-launch-role-<environment>`).

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

