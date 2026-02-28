use crate::{aws, config, project};
use anyhow::{Context, Result};
use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::process::Command;

pub fn profiles_list(layout: &project::ProjectLayout) -> Result<()> {
    let path = layout.profiles_yaml();
    let pf: config::ProfilesFile = if path.exists() {
        config::load_yaml(&path)?
    } else {
        config::ProfilesFile::default()
    };

    if pf.profiles.is_empty() {
        println!("(no profiles configured)");
        return Ok(());
    }

    println!("{:<12} {:<24} {:<12} {}", "ENV", "AWS_PROFILE", "REGION", "ACCOUNT_ID");
    for (env, p) in pf.profiles {
        println!(
            "{:<12} {:<24} {:<12} {}",
            env, p.aws_profile, p.aws_region, p.account_id
        );
    }
    Ok(())
}

pub async fn profiles_set(
    layout: &project::ProjectLayout,
    environment: String,
    aws_profile: String,
    region: String,
    account_id: String,
    verify: bool,
    sso_login: bool,
) -> Result<()> {
    // Write first (so verify can reuse connect logic too, if desired).
    let path = layout.profiles_yaml();
    let mut pf: config::ProfilesFile = if path.exists() {
        config::load_yaml(&path)?
    } else {
        config::ProfilesFile::default()
    };
    let existing_product_parameters = pf
        .profiles
        .get(&environment)
        .map(|p| p.product_parameters.clone())
        .unwrap_or_default();

    pf.profiles.insert(
        environment.clone(),
        config::Profile {
            aws_profile: aws_profile.clone(),
            aws_region: region.clone(),
            account_id: account_id.clone(),
            product_parameters: existing_product_parameters,
        },
    );
    config::save_yaml(&path, &pf)?;

    if verify {
        // Reuse the existing STS verification path.
        aws::connect(
            layout,
            environment,
            Some(aws_profile),
            Some(region),
            Some(account_id),
            sso_login,
        )
        .await?;
    }
    Ok(())
}

pub async fn profiles_whoami(layout: &project::ProjectLayout, environment: String) -> Result<()> {
    // Uses connect() verification logic but without writing (it will write same values back).
    // We'll just run connect with no overrides; it will use existing profile values and validate STS.
    aws::connect(layout, environment, None, None, None, false).await
}

pub fn products_list(layout: &project::ProjectLayout) -> Result<()> {
    let catalog: config::CatalogFile = config::load_yaml(&layout.catalog_yaml())
        .with_context(|| format!("load {}", layout.catalog_yaml().display()))?;

    if catalog.products.is_empty() {
        println!("(no products configured)");
        return Ok(());
    }

    println!("{:<16} {:<16} {:<20} {}", "NAME", "PORTFOLIO", "PATH", "DEPS");
    for (name, spec) in catalog.products {
        let deps = if spec.dependencies.is_empty() {
            "-".to_string()
        } else {
            spec.dependencies.join(",")
        };
        let portfolio = if spec.portfolio.is_empty() {
            "-".to_string()
        } else {
            spec.portfolio
        };
        println!("{:<16} {:<16} {:<20} {}", name, portfolio, spec.path, deps);
    }
    Ok(())
}

pub fn products_add(
    layout: &project::ProjectLayout,
    name: String,
    path: Option<String>,
    portfolio: Option<String>,
    description: Option<String>,
    dependencies: Vec<String>,
    outputs: Vec<String>,
    mappings: Vec<String>,
) -> Result<()> {
    let mut catalog: config::CatalogFile = config::load_yaml(&layout.catalog_yaml())
        .with_context(|| format!("load {}", layout.catalog_yaml().display()))?;

    if catalog.products.contains_key(&name) {
        anyhow::bail!("product '{name}' already exists in .deployer/catalog.yaml");
    }

    let product_path = path.clone().unwrap_or_else(|| name.clone());
    let product_dir = layout.products_dir().join(&product_path);
    fs::create_dir_all(&product_dir)
        .with_context(|| format!("create {}", product_dir.display()))?;

    // Parse Param=dep.out mappings
    let mut pm: BTreeMap<String, String> = BTreeMap::new();
    for m in mappings {
        let (k, v) = m
            .split_once('=')
            .with_context(|| format!("invalid --param-mapping '{m}' (expected Param=dep.output)"))?;
        pm.insert(k.to_string(), v.to_string());
    }

    // Write product.yaml (simple schema, used mostly for humans)
    let product_yaml = serde_yaml::to_string(&serde_yaml::Value::Mapping({
        let mut map = serde_yaml::Mapping::new();
        map.insert("name".into(), name.clone().into());
        map.insert(
            "description".into(),
            description.clone().unwrap_or_default().into(),
        );
        map.insert(
            "portfolio".into(),
            portfolio.clone().unwrap_or_default().into(),
        );
        map
    }))
    .context("serialize product.yaml")?;
    fs::write(product_dir.join("product.yaml"), product_yaml).context("write product.yaml")?;

    // Write a valid minimal template.yaml placeholder.
    // Include parameters for any mapped params, and include outputs if requested.
    let mut template = String::new();
    template.push_str("AWSTemplateFormatVersion: '2010-09-09'\n");
    template.push_str(&format!(
        "Description: {}\n\n",
        description
            .clone()
            .unwrap_or_else(|| format!("Service Catalog template for {name}"))
            .replace('\n', " ")
    ));

    template.push_str("Parameters:\n");
    template.push_str("  Environment:\n");
    template.push_str("    Type: String\n");
    template.push_str("    Default: dev\n");
    for param_name in pm.keys() {
        template.push('\n');
        template.push_str(&format!("  {param_name}:\n"));
        template.push_str("    Type: String\n");
        template.push_str(&format!("    Description: Mapped from {}\n", pm[param_name]));
    }

    template.push_str("\nResources:\n");
    template.push_str("  PlaceholderResource:\n");
    template.push_str("    Type: AWS::CloudFormation::WaitConditionHandle\n");

    if !outputs.is_empty() {
        template.push_str("\nOutputs:\n");
        for out_name in &outputs {
            template.push_str(&format!("  {out_name}:\n"));
            template.push_str(&format!("    Description: {out_name}\n"));
            template.push_str("    Value: !Ref PlaceholderResource\n");
            template.push_str("    Export:\n");
            template.push_str(&format!("      Name: !Sub \"${{Environment}}-{out_name}\"\n"));
        }
    }
    fs::write(product_dir.join("template.yaml"), template).context("write template.yaml")?;

    // Update catalog
    catalog.products.insert(
        name.clone(),
        config::ProductSpec {
            path: product_path,
            portfolio: portfolio.unwrap_or_default(),
            launch_role: None,
            launch_role_arn: None,
            ecr_repository: None,
            dependencies,
            parameter_mapping: pm,
            outputs,
            code_param_mapping: BTreeMap::new(),
            test_command: None,
        },
    );
    config::save_yaml(&layout.catalog_yaml(), &catalog)?;

    Ok(())
}

pub fn products_graph(layout: &project::ProjectLayout) -> Result<()> {
    let catalog: config::CatalogFile = config::load_yaml(&layout.catalog_yaml())
        .with_context(|| format!("load {}", layout.catalog_yaml().display()))?;

    if catalog.products.is_empty() {
        println!("(no products configured)");
        return Ok(());
    }

    // Build reverse dep map
    let mut dependents: BTreeMap<String, Vec<String>> =
        catalog.products.keys().map(|k| (k.clone(), vec![])).collect();
    for (name, spec) in &catalog.products {
        for dep in &spec.dependencies {
            if let Some(v) = dependents.get_mut(dep) {
                v.push(name.clone());
            }
        }
    }

    // Roots = no deps
    let mut roots: Vec<String> = catalog
        .products
        .iter()
        .filter_map(|(n, s)| if s.dependencies.is_empty() { Some(n.clone()) } else { None })
        .collect();
    roots.sort();

    fn print_tree(
        node: &str,
        dependents: &BTreeMap<String, Vec<String>>,
        prefix: &str,
        is_last: bool,
        visiting: &mut Vec<String>,
    ) {
        let connector = if is_last { "└── " } else { "├── " };
        if visiting.contains(&node.to_string()) {
            println!("{prefix}{connector}{node} (cycle)");
            return;
        }
        println!("{prefix}{connector}{node}");
        visiting.push(node.to_string());

        let children = dependents.get(node).cloned().unwrap_or_default();
        let mut children = children;
        children.sort();
        for (i, c) in children.iter().enumerate() {
            let child_last = i + 1 == children.len();
            let new_prefix = format!("{prefix}{}", if is_last { "    " } else { "│   " });
            print_tree(c, dependents, &new_prefix, child_last, visiting);
        }

        visiting.pop();
    }

    for (i, r) in roots.iter().enumerate() {
        let last = i + 1 == roots.len();
        print_tree(r, &dependents, "", last, &mut Vec::new());
    }

    Ok(())
}

/// Resolve test command: catalog override, or auto-detect from pyproject.toml / package.json.
fn resolve_test_command(spec: &config::ProductSpec, product_dir: &Path) -> Option<(String, Vec<String>)> {
    if let Some(ref cmd) = spec.test_command {
        // Parse "cmd arg1 arg2" into (cmd, [arg1, arg2])
        let parts: Vec<&str> = cmd.split_whitespace().collect();
        if let Some((first, rest)) = parts.split_first() {
            return Some(((*first).to_string(), rest.iter().map(|s| (*s).to_string()).collect()));
        }
    }

    // Auto-detect: pyproject.toml -> pytest
    if product_dir.join("pyproject.toml").is_file() {
        return Some(("pytest".to_string(), vec!["tests/".to_string()]));
    }

    // Auto-detect: package.json with scripts.test -> npm test
    if let Ok(content) = fs::read_to_string(product_dir.join("package.json")) {
        if let Ok(json) = serde_json::from_str::<serde_json::Value>(&content) {
            if let Some(scripts) = json.get("scripts").and_then(|s| s.as_object()) {
                if scripts.contains_key("test") {
                    return Some(("npm".to_string(), vec!["test".to_string()]));
                }
            }
        }
    }

    None
}

pub fn products_test(layout: &project::ProjectLayout, products: Vec<String>) -> Result<()> {
    let catalog: config::CatalogFile = config::load_yaml(&layout.catalog_yaml())
        .with_context(|| format!("load {}", layout.catalog_yaml().display()))?;

    let candidates: Vec<&String> = if products.is_empty() {
        catalog.products.keys().collect()
    } else {
        let mut out = Vec::new();
        for p in &products {
            if catalog.products.contains_key(p) {
                out.push(p);
            } else {
                anyhow::bail!("product '{p}' not found in catalog");
            }
        }
        out
    };

    let mut any_ran = false;
    for name in candidates {
        let spec = catalog.products.get(name).context("product")?;
        let product_dir = layout.products_dir().join(&spec.path);

        if !product_dir.is_dir() {
            println!("{name}: product dir not found, skipping");
            continue;
        }

        let tests_dir = product_dir.join("tests");
        if !tests_dir.is_dir() {
            println!("{name}: no tests/ directory, skipping");
            continue;
        }

        let Some((cmd, args)) = resolve_test_command(spec, &product_dir) else {
            println!("{name}: no test_command in catalog and could not auto-detect (add pyproject.toml, package.json with scripts.test, or test_command in catalog)");
            continue;
        };

        println!("{name}: running {} {}", cmd, args.join(" "));
        any_ran = true;

        let status = Command::new(&cmd)
            .args(&args)
            .current_dir(&product_dir)
            .status()
            .with_context(|| format!("run test command for {name}"))?;

        if !status.success() {
            anyhow::bail!("{name}: tests failed (exit code {:?})", status.code());
        }
    }

    if !any_ran {
        println!("No products with runnable tests.");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn products_add_rejects_bad_mapping() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("p");
        let layout = crate::project::init_project(&dir, false).unwrap();

        let err = products_add(
            &layout,
            "api".to_string(),
            None,
            None,
            None,
            vec![],
            vec![],
            vec!["BadMapping".to_string()],
        )
        .unwrap_err()
        .to_string();

        assert!(err.contains("invalid --param-mapping"));
    }
}

