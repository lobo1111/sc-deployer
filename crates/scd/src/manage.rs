use crate::{aws, config, project};
use anyhow::{Context, Result};
use std::collections::BTreeMap;
use std::fs;

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

    pf.profiles.insert(
        environment.clone(),
        config::Profile {
            aws_profile: aws_profile.clone(),
            aws_region: region.clone(),
            account_id: account_id.clone(),
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
            ecr_repository: None,
            dependencies,
            parameter_mapping: pm,
            outputs,
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

