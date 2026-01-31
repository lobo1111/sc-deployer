use crate::{config, project, state};
use anyhow::{Context, Result};
use aws_types::region::Region;
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::time::Duration;
use time::format_description::well_known::Rfc3339;

#[derive(Debug, Clone)]
struct AwsEnv {
    environment: String,
    aws_profile: String,
    aws_region: String,
    account_id: String,
}

fn load_env(layout: &project::ProjectLayout, environment: &str) -> Result<AwsEnv> {
    let profiles: config::ProfilesFile = config::load_yaml(&layout.profiles_yaml())
        .with_context(|| format!("load {}", layout.profiles_yaml().display()))?;
    let p = profiles.profiles.get(environment).with_context(|| {
        format!(
            "environment '{}' not configured (run `scd connect -e {}`)",
            environment, environment
        )
    })?;
    Ok(AwsEnv {
        environment: environment.to_string(),
        aws_profile: p.aws_profile.clone(),
        aws_region: p.aws_region.clone(),
        account_id: p.account_id.clone(),
    })
}

fn load_catalog(layout: &project::ProjectLayout) -> Result<config::CatalogFile> {
    config::load_yaml(&layout.catalog_yaml())
        .with_context(|| format!("load {}", layout.catalog_yaml().display()))
}

fn load_bootstrap(layout: &project::ProjectLayout) -> Result<config::BootstrapFile> {
    config::load_yaml(&layout.bootstrap_yaml())
        .with_context(|| format!("load {}", layout.bootstrap_yaml().display()))
}

fn topo_sort(products: &BTreeMap<String, config::ProductSpec>, subset: &BTreeSet<String>) -> Result<Vec<String>> {
    let mut in_degree: BTreeMap<String, usize> = subset.iter().map(|p| (p.clone(), 0)).collect();

    for name in subset {
        for dep in &products[name].dependencies {
            if subset.contains(dep) {
                *in_degree.get_mut(name).unwrap() += 1;
            }
        }
    }

    let mut q: VecDeque<String> = in_degree
        .iter()
        .filter_map(|(k, v)| if *v == 0 { Some(k.clone()) } else { None })
        .collect();
    let mut out = Vec::new();

    while let Some(n) = q.pop_front() {
        out.push(n.clone());
        for other in subset {
            if products[other].dependencies.contains(&n) {
                let e = in_degree.get_mut(other).unwrap();
                *e -= 1;
                if *e == 0 {
                    q.push_back(other.clone());
                }
            }
        }
    }

    if out.len() != subset.len() {
        anyhow::bail!("circular dependency detected");
    }
    Ok(out)
}

pub async fn validate(layout: &project::ProjectLayout, environment: String) -> Result<()> {
    let catalog = load_catalog(layout)?;

    // Cycle detection
    {
        let mut visiting = BTreeSet::new();
        let mut visited = BTreeSet::new();
        fn dfs(
            name: &str,
            products: &BTreeMap<String, config::ProductSpec>,
            visiting: &mut BTreeSet<String>,
            visited: &mut BTreeSet<String>,
        ) -> Result<()> {
            if visited.contains(name) {
                return Ok(());
            }
            if visiting.contains(name) {
                anyhow::bail!("cycle detected at '{name}'");
            }
            visiting.insert(name.to_string());
            for dep in &products[name].dependencies {
                if products.contains_key(dep) {
                    dfs(dep, products, visiting, visited)?;
                }
            }
            visiting.remove(name);
            visited.insert(name.to_string());
            Ok(())
        }

        for name in catalog.products.keys() {
            dfs(name, &catalog.products, &mut visiting, &mut visited)?;
        }
    }

    // Mapping validation: `param: dep.output` must reference declared dep and output
    for (name, spec) in &catalog.products {
        for (param, src) in &spec.parameter_mapping {
            let (dep, output) = src
                .split_once('.')
                .with_context(|| format!("{name}: invalid mapping for {param}: '{src}' (expected dep.output)"))?;
            if !spec.dependencies.contains(&dep.to_string()) {
                anyhow::bail!("{name}: mapping uses '{dep}' but it's not listed in dependencies");
            }
            let dep_spec = catalog
                .products
                .get(dep)
                .with_context(|| format!("{name}: mapping references unknown dependency '{dep}'"))?;
            if !dep_spec.outputs.contains(&output.to_string()) {
                anyhow::bail!(
                    "{name}: mapping references output '{output}' not declared by '{dep}'"
                );
            }
        }
    }

    // Bootstrap state presence
    let bootstrap = load_bootstrap(layout)?;
    let st_path = layout.deployer_dir().join(bootstrap.settings.state_file);
    let st: state::BootstrapState = state::load_json(&st_path)?;
    let env_state = st.environments.get(&environment).with_context(|| {
        format!(
            "environment '{}' not bootstrapped/synced (run `scd sync -e {}`)",
            environment, environment
        )
    })?;

    if env_state.template_bucket.as_ref().and_then(|b| b.name.as_ref()).is_none() {
        anyhow::bail!("bootstrap state missing template bucket (run `scd sync`)");
    }
    Ok(())
}

pub async fn plan(layout: &project::ProjectLayout, _environment: String, products: Vec<String>) -> Result<()> {
    let catalog = load_catalog(layout)?;
    let subset: BTreeSet<String> = if products.is_empty() {
        catalog.products.keys().cloned().collect()
    } else {
        products.into_iter().collect()
    };

    for p in &subset {
        if !catalog.products.contains_key(p) {
            anyhow::bail!("unknown product '{p}'");
        }
    }

    let order = topo_sort(&catalog.products, &subset)?;
    println!("Deployment order:");
    for (i, p) in order.iter().enumerate() {
        println!("  {}. {}", i + 1, p);
    }
    Ok(())
}

fn generate_version() -> String {
    // Default format: %Y.%m.%d.%H%M%S
    let now = time::OffsetDateTime::now_utc();
    format!(
        "{:04}.{:02}.{:02}.{:02}{:02}{:02}",
        now.year(),
        u8::from(now.month()),
        now.day(),
        now.hour(),
        now.minute(),
        now.second()
    )
}

fn resolve_parameters(
    catalog: &config::CatalogFile,
    deploy_env: &state::DeployEnvState,
    product_name: &str,
) -> Result<BTreeMap<String, String>> {
    let spec = &catalog.products[product_name];
    let mut out = BTreeMap::new();
    for (param, src) in &spec.parameter_mapping {
        let (dep, output) = src.split_once('.').context("invalid mapping")?;
        let dep_state = deploy_env
            .products
            .get(dep)
            .with_context(|| format!("missing deployed state for dependency '{dep}'"))?;
        let val = dep_state
            .outputs
            .get(output)
            .with_context(|| format!("missing output '{output}' on dependency '{dep}'"))?;
        out.insert(param.clone(), val.clone());
    }
    Ok(out)
}

pub async fn publish(
    layout: &project::ProjectLayout,
    environment: String,
    products: Vec<String>,
    dry_run: bool,
    _force: bool,
) -> Result<()> {
    validate(layout, environment.clone()).await?;

    let env = load_env(layout, &environment)?;
    let catalog = load_catalog(layout)?;
    let bootstrap = load_bootstrap(layout)?;

    let st_path = layout.deployer_dir().join(bootstrap.settings.state_file);
    let bst: state::BootstrapState = state::load_json(&st_path)?;
    let env_bootstrap = bst
        .environments
        .get(&environment)
        .context("missing bootstrap env state")?;
    let bucket_name = env_bootstrap
        .template_bucket
        .as_ref()
        .and_then(|b| b.name.as_ref())
        .context("missing template bucket name in bootstrap state")?
        .clone();

    let shared = aws_config::from_env()
        .profile_name(&env.aws_profile)
        .region(Region::new(env.aws_region.clone()))
        .load()
        .await;
    let s3 = aws_sdk_s3::Client::new(&shared);
    let sc = aws_sdk_servicecatalog::Client::new(&shared);

    let version = generate_version();
    let deploy_state_path = layout.deployer_dir().join(catalog.settings.state_file.clone());
    let mut dst: state::DeployState = state::load_json(&deploy_state_path)?;
    let env_state = dst
        .environments
        .entry(environment.clone())
        .or_insert_with(state::DeployEnvState::default);

    let to_publish: Vec<String> = if products.is_empty() {
        catalog.products.keys().cloned().collect()
    } else {
        products
    };

    for p in &to_publish {
        let product_id = env_bootstrap
            .products
            .get(p)
            .and_then(|r| r.id.clone())
            .with_context(|| format!("missing product id for '{p}' in bootstrap state (run `scd sync`)"))?;

        let product_path = layout.products_dir().join(&catalog.products[p].path);
        let template_path = product_path.join("template.yaml");
        let template_body = std::fs::read(&template_path)
            .with_context(|| format!("read {}", template_path.display()))?;

        let s3_key = format!("{}/{}/template.yaml", p, version);
        let template_url = format!(
            "https://{bucket_name}.s3.{}.amazonaws.com/{s3_key}",
            env.aws_region
        );

        println!("Publishing {p} as version {version}");
        if dry_run {
            println!("  [DRY RUN] upload s3://{bucket_name}/{s3_key}");
            println!("  [DRY RUN] create provisioning artifact for product {product_id}");
            continue;
        }

        s3.put_object()
            .bucket(&bucket_name)
            .key(&s3_key)
            .body(aws_sdk_s3::primitives::ByteStream::from(template_body))
            .content_type("application/x-yaml")
            .send()
            .await
            .context("put_object template")?;

        sc.create_provisioning_artifact()
            .product_id(&product_id)
            .parameters(
                aws_sdk_servicecatalog::types::ProvisioningArtifactProperties::builder()
                    .name(&version)
                    .description(format!("Version {version}"))
                    .r#type(aws_sdk_servicecatalog::types::ProvisioningArtifactType::CloudFormationTemplate)
                    .info("LoadTemplateFromURL", template_url)
                    .build(),
            )
            .send()
            .await
            .context("create_provisioning_artifact")?;

        let ps = env_state
            .products
            .entry(p.clone())
            .or_insert_with(state::DeployProductState::default);
        ps.version = Some(version.clone());
        ps.published_at = Some(
            time::OffsetDateTime::now_utc()
                .format(&Rfc3339)
                .unwrap_or_else(|_| "unknown".to_string()),
        );
    }

    if !dry_run {
        state::save_json(&deploy_state_path, &dst)?;
    }
    Ok(())
}

async fn get_provisioning_artifact_id(
    sc: &aws_sdk_servicecatalog::Client,
    product_id: &str,
    version: &str,
) -> Result<String> {
    let out = sc
        .list_provisioning_artifacts()
        .product_id(product_id)
        .send()
        .await
        .context("list_provisioning_artifacts")?;
    for a in out.provisioning_artifact_details() {
        if a.name() == Some(version) {
            return Ok(a.id().unwrap_or_default().to_string());
        }
    }
    anyhow::bail!("provisioning artifact not found for version {version}");
}

async fn get_launch_path_id(sc: &aws_sdk_servicecatalog::Client, product_id: &str) -> Result<String> {
    let out = sc
        .list_launch_paths()
        .product_id(product_id)
        .send()
        .await
        .context("list_launch_paths")?;
    let lp = out
        .launch_path_summaries()
        .first()
        .context("no launch paths found (check portfolio association/access)")?;
    Ok(lp.id().unwrap_or_default().to_string())
}

async fn wait_record(sc: &aws_sdk_servicecatalog::Client, record_id: &str) -> Result<()> {
    let mut waited = Duration::from_secs(0);
    loop {
        let out = sc
            .describe_record()
            .id(record_id)
            .send()
            .await
            .context("describe_record")?;
        let status = out
            .record_detail()
            .and_then(|d| d.status())
            .map(|s| s.as_str())
            .unwrap_or("UNKNOWN");
        match status {
            "SUCCEEDED" => return Ok(()),
            "FAILED" | "IN_PROGRESS_IN_ERROR" => {
                anyhow::bail!("record {record_id} failed: {status}");
            }
            _ => {}
        }
        tokio::time::sleep(Duration::from_secs(10)).await;
        waited += Duration::from_secs(10);
        if waited > Duration::from_secs(1200) {
            anyhow::bail!("record {record_id} timed out");
        }
    }
}

async fn get_outputs(sc: &aws_sdk_servicecatalog::Client, pp_id: &str) -> Result<BTreeMap<String, String>> {
    let out = sc
        .get_provisioned_product_outputs()
        .provisioned_product_id(pp_id)
        .send()
        .await
        .context("get_provisioned_product_outputs")?;
    let mut m = BTreeMap::new();
    for o in out.outputs() {
        if let (Some(k), Some(v)) = (o.output_key(), o.output_value()) {
            if k != "CloudformationStackARN" {
                m.insert(k.to_string(), v.to_string());
            }
        }
    }
    Ok(m)
}

pub async fn apply(
    layout: &project::ProjectLayout,
    environment: String,
    products: Vec<String>,
    dry_run: bool,
) -> Result<()> {
    validate(layout, environment.clone()).await?;

    let env = load_env(layout, &environment)?;
    let catalog = load_catalog(layout)?;
    let bootstrap = load_bootstrap(layout)?;

    let st_path = layout.deployer_dir().join(bootstrap.settings.state_file);
    let bst: state::BootstrapState = state::load_json(&st_path)?;
    let env_bootstrap = bst
        .environments
        .get(&environment)
        .context("missing bootstrap env state")?;

    let deploy_state_path = layout.deployer_dir().join(catalog.settings.state_file.clone());
    let mut dst: state::DeployState = state::load_json(&deploy_state_path)?;
    let env_state = dst
        .environments
        .entry(environment.clone())
        .or_insert_with(state::DeployEnvState::default);

    let subset: BTreeSet<String> = if products.is_empty() {
        catalog.products.keys().cloned().collect()
    } else {
        products.into_iter().collect()
    };
    let order = topo_sort(&catalog.products, &subset)?;

    let shared = aws_config::from_env()
        .profile_name(&env.aws_profile)
        .region(Region::new(env.aws_region.clone()))
        .load()
        .await;
    let sc = aws_sdk_servicecatalog::Client::new(&shared);

    for p in order {
        let ps = env_state.products.get(&p).cloned().unwrap_or_default();
        let version = ps
            .version
            .clone()
            .with_context(|| format!("product '{p}' not published yet (run `scd deploy publish -e {environment}`)"))?;
        let product_id = env_bootstrap
            .products
            .get(&p)
            .and_then(|r| r.id.clone())
            .with_context(|| format!("missing product id for '{p}' in bootstrap state (run `scd sync`)"))?;

        let artifact_id = get_provisioning_artifact_id(&sc, &product_id, &version).await?;
        let path_id = get_launch_path_id(&sc, &product_id).await?;

        let mut params = resolve_parameters(&catalog, env_state, &p)?;
        params.insert("Environment".to_string(), environment.clone());
        let prov_params: Vec<aws_sdk_servicecatalog::types::ProvisioningParameter> = params
            .iter()
            .map(|(k, v)| {
                aws_sdk_servicecatalog::types::ProvisioningParameter::builder()
                    .key(k)
                    .value(v)
                    .build()
            })
            .collect();
        let update_params: Vec<aws_sdk_servicecatalog::types::UpdateProvisioningParameter> = params
            .iter()
            .map(|(k, v)| {
                aws_sdk_servicecatalog::types::UpdateProvisioningParameter::builder()
                    .key(k)
                    .value(v)
                    .build()
            })
            .collect();

        let provisioned_name = format!("{}-{}", environment, p);

        println!("Applying {p} (version {version})");
        if dry_run {
            println!("  [DRY RUN] provision/update {provisioned_name}");
            continue;
        }

        let existing_pp = env_state
            .products
            .get(&p)
            .and_then(|s| s.provisioned_product_id.clone());

        let record_id = if let Some(pp_id) = existing_pp.clone() {
            let out = sc
                .update_provisioned_product()
                .provisioned_product_id(pp_id)
                .product_id(&product_id)
                .provisioning_artifact_id(&artifact_id)
                .path_id(&path_id)
                .set_provisioning_parameters(Some(update_params.clone()))
                .send()
                .await
                .context("update_provisioned_product")?;
            out.record_detail()
                .and_then(|d| d.record_id())
                .unwrap_or_default()
                .to_string()
        } else {
            let out = sc
                .provision_product()
                .product_id(&product_id)
                .provisioning_artifact_id(&artifact_id)
                .path_id(&path_id)
                .provisioned_product_name(&provisioned_name)
                .set_provisioning_parameters(Some(prov_params))
                .send()
                .await
                .context("provision_product")?;
            out.record_detail()
                .and_then(|d| d.record_id())
                .unwrap_or_default()
                .to_string()
        };

        wait_record(&sc, &record_id).await?;

        // Resolve provisioned product id
        let pp_id = if let Some(pp) = existing_pp {
            pp
        } else {
            // Best-effort: search by name
            let out = sc
                .search_provisioned_products()
                .filters(
                    aws_sdk_servicecatalog::types::ProvisionedProductViewFilterBy::SearchQuery,
                    vec![format!("name:{provisioned_name}")],
                )
                .send()
                .await
                .context("search_provisioned_products")?;
            let found = out
                .provisioned_products()
                .iter()
                .find(|pp| pp.name() == Some(provisioned_name.as_str()))
                .context("could not find provisioned product after provisioning")?;
            found.id().unwrap_or_default().to_string()
        };

        let outputs = get_outputs(&sc, &pp_id).await.unwrap_or_default();

        let ps_mut = env_state
            .products
            .entry(p.clone())
            .or_insert_with(state::DeployProductState::default);
        ps_mut.provisioned_product_id = Some(pp_id.clone());
        ps_mut.provisioned_product_name = Some(provisioned_name);
        ps_mut.deployed_at = Some(
            time::OffsetDateTime::now_utc()
                .format(&Rfc3339)
                .unwrap_or_else(|_| "unknown".to_string()),
        );
        ps_mut.outputs = outputs;
    }

    if !dry_run {
        state::save_json(&deploy_state_path, &dst)?;
    }
    Ok(())
}

pub async fn status(layout: &project::ProjectLayout, environment: String) -> Result<()> {
    let catalog = load_catalog(layout)?;
    let deploy_state_path = layout.deployer_dir().join(catalog.settings.state_file.clone());
    let dst: state::DeployState = state::load_json(&deploy_state_path)?;
    let env_state = dst.environments.get(&environment).cloned().unwrap_or_default();

    println!("Status: {environment}");
    for (name, _) in &catalog.products {
        let ps = env_state.products.get(name);
        let version = ps.and_then(|p| p.version.clone()).unwrap_or("-".to_string());
        let deployed = ps
            .and_then(|p| p.deployed_at.clone())
            .unwrap_or("-".to_string());
        println!("{name:<20} version={version:<18} deployed_at={deployed}");
    }
    Ok(())
}

pub async fn terminate(
    layout: &project::ProjectLayout,
    environment: String,
    products: Vec<String>,
    dry_run: bool,
    force: bool,
) -> Result<()> {
    if !force && !dry_run {
        anyhow::bail!("terminate is destructive; pass --force to proceed");
    }

    let env = load_env(layout, &environment)?;
    let catalog = load_catalog(layout)?;
    let deploy_state_path = layout.deployer_dir().join(catalog.settings.state_file.clone());
    let mut dst: state::DeployState = state::load_json(&deploy_state_path)?;
    let env_state = dst
        .environments
        .entry(environment.clone())
        .or_insert_with(state::DeployEnvState::default);

    let shared = aws_config::from_env()
        .profile_name(&env.aws_profile)
        .region(Region::new(env.aws_region.clone()))
        .load()
        .await;
    let sc = aws_sdk_servicecatalog::Client::new(&shared);

    let targets: Vec<String> = if products.is_empty() {
        env_state
            .products
            .iter()
            .filter_map(|(k, v)| if v.provisioned_product_id.is_some() { Some(k.clone()) } else { None })
            .collect()
    } else {
        products
    };

    for p in targets {
        let pp_id = match env_state.products.get(&p).and_then(|s| s.provisioned_product_id.clone()) {
            Some(id) => id,
            None => continue,
        };
        println!("Terminating {p} ({pp_id})");
        if dry_run {
            println!("  [DRY RUN] terminate_provisioned_product");
            continue;
        }
        let out = sc
            .terminate_provisioned_product()
            .provisioned_product_id(&pp_id)
            .terminate_token(format!("terminate-{}-{}", p, generate_version()))
            .send()
            .await
            .context("terminate_provisioned_product")?;
        let record_id = out
            .record_detail()
            .and_then(|d| d.record_id())
            .unwrap_or_default()
            .to_string();
        wait_record(&sc, &record_id).await?;

        // Clear state
        if let Some(s) = env_state.products.get_mut(&p) {
            s.provisioned_product_id = None;
            s.provisioned_product_name = None;
            s.deployed_at = None;
            s.outputs.clear();
        }
    }

    if !dry_run {
        state::save_json(&deploy_state_path, &dst)?;
    }
    Ok(())
}

pub async fn destroy(
    layout: &project::ProjectLayout,
    environment: String,
    dry_run: bool,
    force: bool,
) -> Result<()> {
    if !force && !dry_run {
        anyhow::bail!("destroy is destructive; pass --force to proceed");
    }

    // Best-effort teardown using state + config naming conventions.
    let env = load_env(layout, &environment)?;
    let bootstrap = load_bootstrap(layout)?;
    let catalog = load_catalog(layout)?;

    let shared = aws_config::from_env()
        .profile_name(&env.aws_profile)
        .region(Region::new(env.aws_region.clone()))
        .load()
        .await;
    let s3 = aws_sdk_s3::Client::new(&shared);
    let ecr = aws_sdk_ecr::Client::new(&shared);
    let iam = aws_sdk_iam::Client::new(&shared);
    let sc = aws_sdk_servicecatalog::Client::new(&shared);

    // 1) terminate provisioned products (if any)
    let _ = terminate(layout, environment.clone(), vec![], dry_run, true).await;

    // Load bootstrap state (may be missing)
    let bst_path = layout.deployer_dir().join(bootstrap.settings.state_file.clone());
    let bst: state::BootstrapState = state::load_json(&bst_path)?;
    let env_bst = bst.environments.get(&environment).cloned().unwrap_or_default();

    // 2) delete Service Catalog products
    for (name, _) in &catalog.products {
        let product_id = env_bst.products.get(name).and_then(|r| r.id.clone());
        let product_id = match product_id {
            Some(id) => id,
            None => continue,
        };

        println!("Deleting product {name} ({product_id})");
        if dry_run {
            continue;
        }

        // Disassociate from portfolios
        if let Ok(out) = sc.list_portfolios_for_product().product_id(&product_id).send().await {
            for p in out.portfolio_details() {
                if let Some(pid) = p.id() {
                    let _ = sc
                        .disassociate_product_from_portfolio()
                        .product_id(&product_id)
                        .portfolio_id(pid)
                        .send()
                        .await;
                }
            }
        }

        // Delete provisioning artifacts (best effort)
        if let Ok(out) = sc
            .list_provisioning_artifacts()
            .product_id(&product_id)
            .send()
            .await
        {
            for a in out.provisioning_artifact_details() {
                if let Some(aid) = a.id() {
                    let _ = sc
                        .delete_provisioning_artifact()
                        .product_id(&product_id)
                        .provisioning_artifact_id(aid)
                        .send()
                        .await;
                }
            }
        }

        let _ = sc.delete_product().id(&product_id).send().await;
    }

    // 3) delete portfolios
    for (name, pref) in &env_bst.portfolios {
        let portfolio_id = match pref.id.as_ref() {
            Some(id) => id.clone(),
            None => continue,
        };
        println!("Deleting portfolio {name} ({portfolio_id})");
        if dry_run {
            continue;
        }

        if let Ok(out) = sc
            .list_principals_for_portfolio()
            .portfolio_id(&portfolio_id)
            .send()
            .await
        {
            for pr in out.principals() {
                if let Some(arn) = pr.principal_arn() {
                    let _ = sc
                        .disassociate_principal_from_portfolio()
                        .portfolio_id(&portfolio_id)
                        .principal_arn(arn)
                        .send()
                        .await;
                }
            }
        }

        let _ = sc.delete_portfolio().id(&portfolio_id).send().await;
    }

    // 4) delete ECR repos
    for repo in &bootstrap.ecr_repositories {
        println!("Deleting ECR repo {}", repo.name);
        if dry_run {
            continue;
        }
        let _ = ecr
            .delete_repository()
            .repository_name(&repo.name)
            .force(true)
            .send()
            .await;
    }

    // 5) delete template bucket
    let bucket_name = env_bst
        .template_bucket
        .as_ref()
        .and_then(|b| b.name.clone())
        .unwrap_or_else(|| {
            format!(
                "{}-{}-{}",
                bootstrap.template_bucket.name_prefix, env.account_id, env.aws_region
            )
        });
    println!("Deleting S3 bucket {bucket_name}");
    if !dry_run {
        // Delete objects (best-effort, non-versioned)
        if let Ok(out) = s3.list_objects_v2().bucket(&bucket_name).send().await {
            let mut objs: Vec<aws_sdk_s3::types::ObjectIdentifier> = Vec::new();
            for o in out.contents() {
                if let Some(k) = o.key() {
                    objs.push(
                        aws_sdk_s3::types::ObjectIdentifier::builder()
                            .key(k)
                            .build()
                            .unwrap(),
                    );
                }
            }
            if !objs.is_empty() {
                let _ = s3
                    .delete_objects()
                    .bucket(&bucket_name)
                    .delete(
                        aws_sdk_s3::types::Delete::builder()
                            .set_objects(Some(objs))
                            .build()
                            .unwrap(),
                    )
                    .send()
                    .await;
            }
        }
        let _ = s3.delete_bucket().bucket(&bucket_name).send().await;
    }

    // 6) delete launch role
    let role_name = format!("scd-launch-role-{}", env.environment);
    println!("Deleting IAM role {role_name}");
    if !dry_run {
        if let Ok(out) = iam.list_attached_role_policies().role_name(&role_name).send().await {
            for p in out.attached_policies() {
                if let Some(arn) = p.policy_arn() {
                    let _ = iam
                        .detach_role_policy()
                        .role_name(&role_name)
                        .policy_arn(arn)
                        .send()
                        .await;
                }
            }
        }
        let _ = iam.delete_role().role_name(&role_name).send().await;
    }

    Ok(())
}

