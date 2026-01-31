use anyhow::{Context, Result};
use rmcp::{
    handler::server::tool::ToolRouter,
    handler::server::wrapper::Parameters,
    model::{CallToolResult, Content, ServerCapabilities, ServerInfo},
    ServerHandler, ServiceExt,
    transport::stdio,
    ErrorData as McpError,
};
use rmcp::schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use tokio::process::Command;

#[derive(Debug, Clone, Default, Serialize, Deserialize, JsonSchema)]
struct CommonOpts {
    /// Working directory to run `scd` in.
    ///
    /// Use this when you want to operate on a specific folder (for example a repo root),
    /// or when Cursor's current working directory isn't what you expect.
    cwd: Option<String>,

    /// Explicit project root (a directory that contains `.deployer/`).
    ///
    /// `scd` normally auto-discovers the project by walking upwards from `cwd`.
    /// Provide `project` if discovery fails or if you want to target a different repo.
    project: Option<String>,
}

fn scd_base_args(common: &CommonOpts) -> Vec<String> {
    let mut args = Vec::new();
    if let Some(p) = &common.project {
        args.push("--project".to_string());
        args.push(p.clone());
    }
    args
}

async fn run_scd(common: &CommonOpts, args: Vec<String>) -> Result<CallToolResult, McpError> {
    let mut cmd = Command::new("scd");
    if let Some(cwd) = &common.cwd {
        cmd.current_dir(cwd);
    }
    cmd.args(&args);

    let out = cmd.output().await.map_err(|e| {
        McpError::internal_error(format!("failed to spawn scd: {e}"), None)
    })?;

    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();
    let code = out.status.code().unwrap_or(0);

    let mut text = String::new();
    text.push_str("command: scd");
    for a in &args {
        text.push(' ');
        // naive quoting; good enough for display
        if a.contains(' ') {
            text.push('"');
            text.push_str(a);
            text.push('"');
        } else {
            text.push_str(a);
        }
    }
    text.push('\n');
    text.push_str(&format!("exit_code: {code}\n"));
    if !stdout.trim().is_empty() {
        text.push_str("\nstdout:\n");
        text.push_str(stdout.trim_end());
        text.push('\n');
    }
    if !stderr.trim().is_empty() {
        text.push_str("\nstderr:\n");
        text.push_str(stderr.trim_end());
        text.push('\n');
    }

    Ok(CallToolResult::success(vec![Content::text(text)]))
}

#[derive(Clone)]
struct ScdMcp {
    tool_router: ToolRouter<Self>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
struct InitParams {
    #[serde(flatten)]
    common: CommonOpts,
    /// New project directory name (created under `cwd`).
    name: String,
    /// Create sample product(s) and YAML.
    #[serde(default)]
    sample: bool,
    /// Run interactive prompts (if supported by scd build).
    #[serde(default)]
    interactive: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
struct ConnectParams {
    #[serde(flatten)]
    common: CommonOpts,
    /// Environment name (e.g. "dev", "stage", "prod", "sandbox").
    environment: String,
    /// AWS CLI profile name to use (e.g. "sandbox").
    aws_profile: Option<String>,
    /// AWS region (e.g. "us-east-1").
    region: Option<String>,
    /// Account id override. Usually not needed (STS discovery is preferred).
    account_id: Option<String>,
    /// Trigger `aws sso login --profile <aws_profile>` before verifying identity.
    #[serde(default)]
    sso_login: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
struct SyncParams {
    #[serde(flatten)]
    common: CommonOpts,
    /// Environment name (e.g. "dev", "stage", "prod", "sandbox").
    environment: String,
    /// If true, print intended actions without changing AWS.
    #[serde(default)]
    dry_run: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
struct DestroyParams {
    #[serde(flatten)]
    common: CommonOpts,
    /// Environment name (e.g. "dev", "stage", "prod", "sandbox").
    environment: String,
    /// If true, print intended actions without changing AWS.
    #[serde(default)]
    dry_run: bool,
    /// If true, skip confirmations / best-effort deletes.
    #[serde(default)]
    force: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
struct DeployBaseParams {
    #[serde(flatten)]
    common: CommonOpts,
    /// Environment name (e.g. "dev", "stage", "prod", "sandbox").
    environment: String,
    /// Optional product filter list. If empty, applies to all configured products.
    #[serde(default)]
    products: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
struct DeployPublishParams {
    #[serde(flatten)]
    base: DeployBaseParams,
    /// If true, print intended actions without changing AWS.
    #[serde(default)]
    dry_run: bool,
    /// If true, re-publish even if the same version exists (when supported).
    #[serde(default)]
    force: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
struct DeployApplyParams {
    #[serde(flatten)]
    base: DeployBaseParams,
    /// If true, print intended actions without changing AWS.
    #[serde(default)]
    dry_run: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
struct DeployTerminateParams {
    #[serde(flatten)]
    base: DeployBaseParams,
    /// If true, print intended actions without changing AWS.
    #[serde(default)]
    dry_run: bool,
    /// If true, skip confirmations / best-effort deletes.
    #[serde(default)]
    force: bool,
}

#[rmcp::tool_router]
impl ScdMcp {
    fn new() -> Self {
        Self {
            tool_router: Self::tool_router(),
        }
    }

    #[rmcp::tool(
        description = "Return the installed scd CLI version. Use to verify the tool is installed and reachable in PATH."
    )]
    async fn scd_version(
        &self,
        params: Parameters<CommonOpts>,
    ) -> Result<CallToolResult, McpError> {
        run_scd(&params.0, vec!["--version".into()]).await
    }

    #[rmcp::tool(
        description = "Create a new scd project directory (creates .deployer/, products/, git repo, and Cursor scaffolding: .cursor/mcp.json, .cursor/skills/, .cursor/rules/, AGENTS.md)."
    )]
    async fn scd_init(
        &self,
        params: Parameters<InitParams>,
    ) -> Result<CallToolResult, McpError> {
        let p = params.0;
        let mut args = scd_base_args(&p.common);
        args.extend(["init".into(), "--name".into(), p.name]);
        if p.sample {
            args.push("--sample".into());
        }
        if p.interactive {
            args.push("--interactive".into());
        }
        run_scd(&p.common, args).await
    }

    #[rmcp::tool(
        description = "Show scd project discovery/status (what project root is selected, and key file locations). Use to debug cwd/project targeting."
    )]
    async fn scd_project_status(
        &self,
        params: Parameters<CommonOpts>,
    ) -> Result<CallToolResult, McpError> {
        let common = params.0;
        let mut args = scd_base_args(&common);
        args.push("project-status".into());
        run_scd(&common, args).await
    }

    #[rmcp::tool(
        description = "Configure/verify AWS connectivity for an environment (runs STS GetCallerIdentity; optionally triggers SSO login). Use before sync/deploy to ensure credentials are valid."
    )]
    async fn scd_connect(
        &self,
        params: Parameters<ConnectParams>,
    ) -> Result<CallToolResult, McpError> {
        let p = params.0;
        let mut args = scd_base_args(&p.common);
        args.extend(["connect".into(), "-e".into(), p.environment]);
        if let Some(v) = p.aws_profile {
            args.extend(["--aws-profile".into(), v]);
        }
        if let Some(v) = p.region {
            args.extend(["--region".into(), v]);
        }
        if let Some(v) = p.account_id {
            args.extend(["--account-id".into(), v]);
        }
        if p.sso_login {
            args.push("--sso-login".into());
        }
        run_scd(&p.common, args).await
    }

    #[rmcp::tool(
        description = "Reconcile local YAML desired state into AWS (portfolios, products, buckets, roles, constraints). Prefer running with dry_run first when making changes."
    )]
    async fn scd_sync(
        &self,
        params: Parameters<SyncParams>,
    ) -> Result<CallToolResult, McpError> {
        let p = params.0;
        let mut args = scd_base_args(&p.common);
        args.extend(["sync".into(), "-e".into(), p.environment]);
        if p.dry_run {
            args.push("--dry-run".into());
        }
        run_scd(&p.common, args).await
    }

    #[rmcp::tool(
        description = "Destroy (tear down) all AWS resources managed by scd for an environment. Use dry_run first; use force only when you intend irreversible deletion."
    )]
    async fn scd_destroy(
        &self,
        params: Parameters<DestroyParams>,
    ) -> Result<CallToolResult, McpError> {
        let p = params.0;
        let mut args = scd_base_args(&p.common);
        args.extend(["destroy".into(), "-e".into(), p.environment]);
        if p.dry_run {
            args.push("--dry-run".into());
        }
        if p.force {
            args.push("--force".into());
        }
        run_scd(&p.common, args).await
    }

    fn add_products(args: &mut Vec<String>, products: &[String]) {
        for p in products {
            args.push("-p".into());
            args.push(p.clone());
        }
    }

    #[rmcp::tool(
        description = "Deploy validate: validate product dependency graph and parameter mappings against local YAML (no AWS changes). Use before plan/publish/apply."
    )]
    async fn scd_deploy_validate(
        &self,
        params: Parameters<DeployBaseParams>,
    ) -> Result<CallToolResult, McpError> {
        let p = params.0;
        let mut args = scd_base_args(&p.common);
        args.extend(["deploy".into(), "validate".into(), "-e".into(), p.environment]);
        run_scd(&p.common, args).await
    }

    #[rmcp::tool(
        description = "Deploy plan: compute deployment order (topological sort) from local YAML. Use to understand what will be deployed and in what order."
    )]
    async fn scd_deploy_plan(
        &self,
        params: Parameters<DeployBaseParams>,
    ) -> Result<CallToolResult, McpError> {
        let p = params.0;
        let mut args = scd_base_args(&p.common);
        args.extend(["deploy".into(), "plan".into(), "-e".into(), p.environment]);
        Self::add_products(&mut args, &p.products);
        run_scd(&p.common, args).await
    }

    #[rmcp::tool(
        description = "Deploy publish: upload templates and create/update Service Catalog provisioning artifacts for products. Use dry_run first when uncertain."
    )]
    async fn scd_deploy_publish(
        &self,
        params: Parameters<DeployPublishParams>,
    ) -> Result<CallToolResult, McpError> {
        let p = params.0;
        let mut args = scd_base_args(&p.base.common);
        args.extend([
            "deploy".into(),
            "publish".into(),
            "-e".into(),
            p.base.environment,
        ]);
        Self::add_products(&mut args, &p.base.products);
        if p.dry_run {
            args.push("--dry-run".into());
        }
        if p.force {
            args.push("--force".into());
        }
        run_scd(&p.base.common, args).await
    }

    #[rmcp::tool(
        description = "Deploy apply: provision/update Service Catalog products in the target environment. This changes AWS resources."
    )]
    async fn scd_deploy_apply(
        &self,
        params: Parameters<DeployApplyParams>,
    ) -> Result<CallToolResult, McpError> {
        let p = params.0;
        let mut args = scd_base_args(&p.base.common);
        args.extend(["deploy".into(), "apply".into(), "-e".into(), p.base.environment]);
        Self::add_products(&mut args, &p.base.products);
        if p.dry_run {
            args.push("--dry-run".into());
        }
        run_scd(&p.base.common, args).await
    }

    #[rmcp::tool(
        description = "Deploy status: show currently deployed product versions and timestamps for an environment (reads local state + AWS as needed)."
    )]
    async fn scd_deploy_status(
        &self,
        params: Parameters<DeployBaseParams>,
    ) -> Result<CallToolResult, McpError> {
        let p = params.0;
        let mut args = scd_base_args(&p.common);
        args.extend(["deploy".into(), "status".into(), "-e".into(), p.environment]);
        run_scd(&p.common, args).await
    }

    #[rmcp::tool(
        description = "Deploy terminate: terminate provisioned products (tears down provisioned instances). Use dry_run first; force only when appropriate."
    )]
    async fn scd_deploy_terminate(
        &self,
        params: Parameters<DeployTerminateParams>,
    ) -> Result<CallToolResult, McpError> {
        let p = params.0;
        let mut args = scd_base_args(&p.base.common);
        args.extend([
            "deploy".into(),
            "terminate".into(),
            "-e".into(),
            p.base.environment,
        ]);
        Self::add_products(&mut args, &p.base.products);
        if p.dry_run {
            args.push("--dry-run".into());
        }
        if p.force {
            args.push("--force".into());
        }
        run_scd(&p.base.common, args).await
    }
}

#[rmcp::tool_handler]
impl ServerHandler for ScdMcp {
    fn get_info(&self) -> ServerInfo {
        ServerInfo {
            instructions: Some(
                r#"This MCP server exposes tools that wrap the `scd` CLI (Service Catalog Deployer).

## What scd is for

- Bootstrap a Service Catalog project layout (local files + git)
- Treat `.deployer/*.yaml` as **desired state**
- Sync desired state to AWS (`scd_sync`)
- Publish/apply deployments (`scd_deploy_publish` / `scd_deploy_apply`)
- Tear down everything managed by scd (`scd_destroy`)

## How to use these tools well

- Prefer editing YAML first, then running `scd_sync` (and deploy commands when needed).
- For safety, use `dry_run: true` where available before making AWS changes.
- Provide `cwd` if you want to target a specific folder.
- Provide `project` if scd discovery fails (project = folder containing `.deployer/`)."#
                    .into(),
            ),
            capabilities: ServerCapabilities::builder().enable_tools().build(),
            ..Default::default()
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let service = ScdMcp::new()
        .serve(stdio())
        .await
        .context("start MCP stdio service")?;

    service.waiting().await.context("MCP service wait")?;
    Ok(())
}

