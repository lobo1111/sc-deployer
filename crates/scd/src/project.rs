use anyhow::{Context, Result};
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::process::Command;

#[derive(Debug, Clone)]
pub struct ProjectLayout {
    pub root: PathBuf,
}

impl ProjectLayout {
    pub fn deployer_dir(&self) -> PathBuf {
        self.root.join(".deployer")
    }
    pub fn profiles_yaml(&self) -> PathBuf {
        self.deployer_dir().join("profiles.yaml")
    }
    pub fn bootstrap_yaml(&self) -> PathBuf {
        self.deployer_dir().join("bootstrap.yaml")
    }
    pub fn catalog_yaml(&self) -> PathBuf {
        self.deployer_dir().join("catalog.yaml")
    }
    pub fn products_dir(&self) -> PathBuf {
        self.root.join("products")
    }
    pub fn git_dir(&self) -> PathBuf {
        self.root.join(".git")
    }
    pub fn gitignore(&self) -> PathBuf {
        self.root.join(".gitignore")
    }

    pub fn cursor_dir(&self) -> PathBuf {
        self.root.join(".cursor")
    }
}

pub fn discover_project_root(start: &Path) -> Option<PathBuf> {
    let mut cur = Some(start);
    while let Some(p) = cur {
        let deployer_dir = p.join(".deployer");
        if deployer_dir.is_dir()
            && (deployer_dir.join("catalog.yaml").is_file()
                || deployer_dir.join("bootstrap.yaml").is_file())
        {
            return Some(p.to_path_buf());
        }
        cur = p.parent();
    }
    None
}

pub fn load_layout(project_override: Option<PathBuf>) -> Result<ProjectLayout> {
    let root = if let Some(p) = project_override {
        p
    } else {
        let cwd = std::env::current_dir().context("get current working directory")?;
        discover_project_root(&cwd)
            .with_context(|| format!("could not find project root from {}", cwd.display()))?
    };

    Ok(ProjectLayout { root })
}

pub fn project_dir_from_name(name: &str) -> Result<PathBuf> {
    if name.trim().is_empty() {
        anyhow::bail!("--name cannot be empty");
    }

    let p = Path::new(name);
    let components: Vec<Component<'_>> = p.components().collect();
    if components.len() != 1 {
        anyhow::bail!("--name must be a single directory name (no slashes)");
    }
    match components[0] {
        Component::Normal(_) => {}
        _ => anyhow::bail!("--name must be a normal directory name"),
    }

    let cwd = std::env::current_dir().context("get current working directory")?;
    Ok(cwd.join(name))
}

pub fn init_project(dir: &Path, sample: bool) -> Result<ProjectLayout> {
    if dir.exists() {
        anyhow::bail!("directory already exists: {}", dir.display());
    }
    fs::create_dir_all(dir).with_context(|| format!("create directory {}", dir.display()))?;

    let layout = ProjectLayout {
        root: dir.to_path_buf(),
    };

    fs::create_dir_all(layout.deployer_dir())
        .with_context(|| format!("create {}", layout.deployer_dir().display()))?;
    fs::create_dir_all(layout.products_dir())
        .with_context(|| format!("create {}", layout.products_dir().display()))?;

    write_file_if_missing(
        &layout.profiles_yaml(),
        r#"# AWS profiles configuration

profiles: {}
"#,
    )?;

    write_file_if_missing(
        &layout.bootstrap_yaml(),
        r#"settings:
  state_file: .bootstrap-state.json

template_bucket:
  name_prefix: sc-templates
  versioning: true
  encryption: AES256

ecr_repositories: []

portfolios: {}
"#,
    )?;

    write_file_if_missing(
        &layout.catalog_yaml(),
        r#"settings:
  state_file: .deploy-state.json
  version_format: "%Y.%m.%d.%H%M%S"

products: {}
"#,
    )?;

    ensure_gitignore_has_lines(
        &layout.gitignore(),
        &[
            "# scd state (sensitive)",
            ".deployer/.bootstrap-state.json",
            ".deployer/.deploy-state.json",
            "",
            "# Rust",
            "target/",
        ],
    )?;

    // Initialize git repo if needed
    if !layout.git_dir().exists() {
        // Try modern git first, then fall back and rename branch.
        let status = Command::new("git")
            .args(["init", "-b", "main"])
            .current_dir(&layout.root)
            .status();

        match status {
            Ok(s) if s.success() => {}
            _ => {
                let s2 = Command::new("git")
                    .arg("init")
                    .current_dir(&layout.root)
                    .status()
                    .context("failed to run `git init` (is git installed?)")?;
                if !s2.success() {
                    anyhow::bail!("`git init` failed with exit code: {s2}");
                }
                // Ensure branch is main (works even if it's already main).
                let _ = Command::new("git")
                    .args(["branch", "-M", "main"])
                    .current_dir(&layout.root)
                    .status();
            }
        }
    } else {
        // Best-effort: ensure existing repo default branch is main.
        let _ = Command::new("git")
            .args(["branch", "-M", "main"])
            .current_dir(&layout.root)
            .status();
    }

    if sample {
        create_sample_product(&layout)?;
    }

    write_cursor_scaffold(&layout)?;

    Ok(layout)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn project_dir_from_name_rejects_paths() {
        assert!(project_dir_from_name("").is_err());
        assert!(project_dir_from_name(".").is_err());
        assert!(project_dir_from_name("..").is_err());
        assert!(project_dir_from_name("a/b").is_err());
    }
}

fn write_file_if_missing(path: &Path, contents: &str) -> Result<()> {
    if path.exists() {
        return Ok(());
    }
    fs::write(path, contents).with_context(|| format!("write {}", path.display()))?;
    Ok(())
}

fn ensure_gitignore_has_lines(path: &Path, lines: &[&str]) -> Result<()> {
    let existing = if path.exists() {
        fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?
    } else {
        String::new()
    };

    let mut out = existing.clone();
    for line in lines {
        if !out.lines().any(|l| l.trim_end() == *line) {
            if !out.ends_with('\n') && !out.is_empty() {
                out.push('\n');
            }
            out.push_str(line);
            out.push('\n');
        }
    }

    if out != existing {
        fs::write(path, out).with_context(|| format!("write {}", path.display()))?;
    }

    Ok(())
}

fn create_sample_product(layout: &ProjectLayout) -> Result<()> {
    let name = "sample";
    let product_dir = layout.products_dir().join(name);
    fs::create_dir_all(&product_dir)
        .with_context(|| format!("create {}", product_dir.display()))?;

    write_file_if_missing(
        &product_dir.join("product.yaml"),
        r#"name: sample
description: Sample product created by scd
portfolio: ""

parameters:
  Environment:
    type: String
    description: Environment name
    default: dev
    required: true

outputs:
  SampleOutput:
    description: Example output
    export: true
"#,
    )?;

    write_file_if_missing(
        &product_dir.join("template.yaml"),
        r#"AWSTemplateFormatVersion: '2010-09-09'
Description: Sample product created by scd

Parameters:
  Environment:
    Type: String
    Default: dev

Resources:
  PlaceholderResource:
    Type: AWS::CloudFormation::WaitConditionHandle

Outputs:
  SampleOutput:
    Description: Example output
    Value: !Ref PlaceholderResource
    Export:
      Name: !Sub "${Environment}-SampleOutput"
"#,
    )?;

    // Minimal update: if catalog.yaml exists and is empty, leave it; full catalog editing comes later.
    Ok(())
}

fn write_cursor_scaffold(layout: &ProjectLayout) -> Result<()> {
    let cursor_dir = layout.cursor_dir();
    fs::create_dir_all(&cursor_dir).with_context(|| format!("create {}", cursor_dir.display()))?;

    // MCP config: prefer the installed `scd-mcp` binary (fast startup, no toolchain required).
    write_file_if_missing(
        &cursor_dir.join("mcp.json"),
        r#"{
  "mcpServers": {
    "scd": {
      "type": "stdio",
      "command": "scd-mcp",
      "args": [],
      "env": {
        "PATH": "${env:PATH}:${userHome}/.local/bin",
        "RUST_LOG": "warn"
      }
    }
  }
}
"#,
    )?;

    let rules_dir = cursor_dir.join("rules");
    fs::create_dir_all(&rules_dir).with_context(|| format!("create {}", rules_dir.display()))?;
    write_file_if_missing(
        &rules_dir.join("scd.mdc"),
        r#"---
description: Use the scd MCP tools for Service Catalog workflows
alwaysApply: true
---

# scd + Cursor MCP usage

Use the **scd MCP tools** (tools named `scd_*`) for anything related to AWS Service Catalog:
- bootstrapping a project (`scd_init`)
- verifying AWS identity (`scd_connect`)
- syncing YAML desired state to AWS (`scd_sync`)
- publish/apply/terminate deployments (`scd_deploy_*`)
- teardown (`scd_destroy`)

## Source of truth

- Treat `.deployer/*.yaml` as **desired state**.
- Prefer changing YAML, then running `scd_sync` (and `scd_deploy_publish/apply` when needed).

## Safety

- Prefer `dry_run` first when available.
- Avoid manual AWS Console / ad-hoc AWS CLI changes unless explicitly requested (they will drift from YAML).
"#,
    )?;

    // Skill package (Agent Skills standard)
    let skills_dir = cursor_dir.join("skills").join("scd-mcp");
    fs::create_dir_all(&skills_dir).with_context(|| format!("create {}", skills_dir.display()))?;
    write_file_if_missing(
        &skills_dir.join("SKILL.md"),
        r#"---
name: scd-mcp
description: Operate AWS Service Catalog projects using the scd MCP tools (scd_init/scd_connect/scd_sync/scd_deploy_*/scd_destroy). Use when bootstrapping a project, editing `.deployer/*.yaml`, syncing portfolios/products, deploying, or tearing down.
---

# scd MCP workflow

## When to use

Use this skill when the user asks to:
- bootstrap a new Service Catalog project
- add/edit portfolios or products (YAML)
- sync desired state to AWS
- publish/apply/terminate deployments
- destroy/clean up all managed resources

## Preferred approach

1. **Edit YAML** under `.deployer/` and `products/` (desired state).
2. Run `scd_sync` to reconcile AWS.
3. Run `scd_deploy_publish` (template/provisioning artifacts), then `scd_deploy_apply`.
4. Use `scd_deploy_status` for visibility.
5. Use `scd_destroy` for full teardown (use `force` only when appropriate).

## MCP tool usage notes

- Provide `cwd` to run in a specific folder.
- If discovery fails, provide `project` explicitly (folder that contains `.deployer/`).
- For risky operations, use `dry_run` first where supported.
"#,
    )?;

    write_file_if_missing(
        &layout.root.join("AGENTS.md"),
        r#"# Agent guidance (scd-managed repo)

This repository is managed using **scd** (Service Catalog Deployer).

- Desired state lives in `.deployer/*.yaml` and `products/**`.
- In Cursor, prefer MCP tools named `scd_*` for Service Catalog operations (sync/deploy/destroy).
- Avoid ad-hoc AWS changes that would drift from YAML, unless explicitly requested.
"#,
    )?;

    Ok(())
}

