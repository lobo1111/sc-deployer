use crate::{config, project, state};
use anyhow::{Context, Result};
use aws_types::region::Region;
use std::collections::BTreeMap;
use std::process::Command;
use time::format_description::well_known::Rfc3339;

const TAG_MANAGED_BY_KEY: &str = "ManagedBy";
const TAG_MANAGED_BY_VALUE: &str = "scd";
const TAG_ENV_KEY: &str = "Environment";

pub struct AwsEnv {
    pub environment: String,
    pub aws_profile: String,
    pub aws_region: String,
    pub account_id: String,
}

pub async fn connect(
    layout: &project::ProjectLayout,
    environment: String,
    aws_profile: Option<String>,
    region: Option<String>,
    account_id: Option<String>,
    sso_login: bool,
) -> Result<()> {
    let profiles_path = layout.deployer_dir().join("profiles.yaml");
    let mut profiles: config::ProfilesFile = if profiles_path.exists() {
        config::load_yaml(&profiles_path)?
    } else {
        config::ProfilesFile::default()
    };

    let existing = profiles.profiles.get(&environment).cloned();

    let resolved_profile = aws_profile
        .or_else(|| existing.as_ref().map(|p| p.aws_profile.clone()))
        .context("missing --aws-profile and no existing profile configured for this environment")?;
    let resolved_region = region
        .or_else(|| existing.as_ref().map(|p| p.aws_region.clone()))
        .context("missing --region and no existing region configured for this environment")?;

    if sso_login {
        // Best-effort SSO login. This requires AWS CLI installed.
        let status = Command::new("aws")
            .args(["sso", "login", "--profile", &resolved_profile])
            .status()
            .context("failed to run `aws sso login` (is AWS CLI installed?)")?;
        if !status.success() {
            anyhow::bail!("`aws sso login` failed with exit code: {status}");
        }
    }

    // Verify with STS GetCallerIdentity
    let shared = aws_config::from_env()
        .profile_name(&resolved_profile)
        .region(Region::new(resolved_region.clone()))
        .load()
        .await;
    let sts = aws_sdk_sts::Client::new(&shared);
    let ident = sts
        .get_caller_identity()
        .send()
        .await
        .context("STS GetCallerIdentity failed")?;

    let sts_account = ident.account().unwrap_or_default().to_string();
    let resolved_account = account_id
        .or_else(|| existing.as_ref().map(|p| p.account_id.clone()))
        .unwrap_or_else(|| sts_account.clone());

    if !resolved_account.is_empty() && !sts_account.is_empty() && resolved_account != sts_account {
        anyhow::bail!(
            "account mismatch: configured {} but STS returned {}",
            resolved_account,
            sts_account
        );
    }

    profiles.profiles.insert(
        environment.clone(),
        config::Profile {
            aws_profile: resolved_profile,
            aws_region: resolved_region,
            account_id: resolved_account,
        },
    );

    config::save_yaml(&profiles_path, &profiles)?;
    Ok(())
}

pub async fn sync(layout: &project::ProjectLayout, environment: String, dry_run: bool) -> Result<()> {
    let env = load_env(layout, &environment)?;

    let bootstrap_path = layout.deployer_dir().join("bootstrap.yaml");
    let catalog_path = layout.deployer_dir().join("catalog.yaml");
    let bootstrap: config::BootstrapFile = config::load_yaml(&bootstrap_path)
        .with_context(|| format!("load {}", bootstrap_path.display()))?;
    let catalog: config::CatalogFile = config::load_yaml(&catalog_path)
        .with_context(|| format!("load {}", catalog_path.display()))?;

    let shared = aws_config::from_env()
        .profile_name(&env.aws_profile)
        .region(Region::new(env.aws_region.clone()))
        .load()
        .await;

    let s3 = aws_sdk_s3::Client::new(&shared);
    let ecr = aws_sdk_ecr::Client::new(&shared);
    let iam = aws_sdk_iam::Client::new(&shared);
    let sc = aws_sdk_servicecatalog::Client::new(&shared);

    let state_path = layout.deployer_dir().join(bootstrap.settings.state_file);
    let mut st: state::BootstrapState = state::load_json(&state_path)?;
    let env_state = st
        .environments
        .entry(environment.clone())
        .or_insert_with(state::BootstrapEnvState::default);
    env_state.account_id = env.account_id.clone();
    env_state.region = env.aws_region.clone();

    // 1) Template bucket
    let bucket_name = format!(
        "{}-{}-{}",
        bootstrap.template_bucket.name_prefix, env.account_id, env.aws_region
    );
    ensure_template_bucket(
        &s3,
        &bucket_name,
        &env,
        &bootstrap.template_bucket,
        dry_run,
    )
    .await?;
    env_state.template_bucket = Some(state::ResourceRef {
        name: Some(bucket_name.clone()),
        arn: Some(format!("arn:aws:s3:::{bucket_name}")),
        ..Default::default()
    });

    // 2) ECR repositories
    let mut ecr_refs = BTreeMap::new();
    for repo in &bootstrap.ecr_repositories {
        let rr = ensure_ecr_repo(&ecr, repo, &env, dry_run).await?;
        ecr_refs.insert(repo.name.clone(), rr);
    }
    env_state.ecr_repositories = ecr_refs;

    // 3) Portfolios
    let mut portfolio_refs = BTreeMap::new();
    for (key, spec) in &bootstrap.portfolios {
        let r = ensure_portfolio(&sc, key, spec, &env, dry_run).await?;
        portfolio_refs.insert(key.clone(), r);
    }
    env_state.portfolios = portfolio_refs;

    // 4) Launch role
    let launch_role = ensure_launch_role(&iam, &env, dry_run).await?;
    env_state.launch_role = Some(launch_role.clone());

    // 5) Products (placeholder) + associations + launch constraints
    let mut product_refs = BTreeMap::new();
    for (name, spec) in &catalog.products {
        let pr = ensure_product(
            &sc,
            &s3,
            name,
            spec,
            &bucket_name,
            &env,
            dry_run,
        )
        .await?;

        // Associate to portfolio if specified
        if !spec.portfolio.is_empty() {
            if let Some(portfolio) = env_state.portfolios.get(&spec.portfolio) {
                if let (Some(product_id), Some(portfolio_id)) = (pr.id.clone(), portfolio.id.clone()) {
                    ensure_product_in_portfolio(&sc, &product_id, &portfolio_id, dry_run).await?;
                    if let (Some(role_arn), Some(product_name)) =
                        (launch_role.arn.clone(), Some(name.clone()))
                    {
                        ensure_launch_constraint(
                            &sc,
                            &portfolio_id,
                            &product_id,
                            &role_arn,
                            &product_name,
                            dry_run,
                        )
                        .await?;
                    }
                }
            } else {
                anyhow::bail!(
                    "product '{}' references unknown portfolio '{}' (in bootstrap.yaml)",
                    name,
                    spec.portfolio
                );
            }
        }

        product_refs.insert(name.clone(), pr);
    }
    env_state.products = product_refs;

    // Timestamp + save state
    let now = time::OffsetDateTime::now_utc()
        .format(&Rfc3339)
        .unwrap_or_else(|_| "unknown".to_string());
    env_state.bootstrapped_at = Some(now);

    if !dry_run {
        state::save_json(&state_path, &st)?;
    }

    Ok(())
}

fn load_env(layout: &project::ProjectLayout, environment: &str) -> Result<AwsEnv> {
    let profiles_path = layout.deployer_dir().join("profiles.yaml");
    let profiles: config::ProfilesFile = config::load_yaml(&profiles_path)
        .with_context(|| format!("load {}", profiles_path.display()))?;
    let p = profiles
        .profiles
        .get(environment)
        .with_context(|| format!("environment '{}' not found in .deployer/profiles.yaml (run `scd connect -e {}`)", environment, environment))?;

    Ok(AwsEnv {
        environment: environment.to_string(),
        aws_profile: p.aws_profile.clone(),
        aws_region: p.aws_region.clone(),
        account_id: p.account_id.clone(),
    })
}

async fn ensure_template_bucket(
    s3: &aws_sdk_s3::Client,
    bucket_name: &str,
    env: &AwsEnv,
    spec: &config::TemplateBucket,
    dry_run: bool,
) -> Result<()> {
    let exists = s3.head_bucket().bucket(bucket_name).send().await.is_ok();
    if !exists {
        if dry_run {
            println!("[DRY RUN] create s3 bucket {bucket_name}");
        } else {
            let mut req = s3.create_bucket().bucket(bucket_name);
            if env.aws_region != "us-east-1" {
                req = req.create_bucket_configuration(
                    aws_sdk_s3::types::CreateBucketConfiguration::builder()
                        .location_constraint(
                            aws_sdk_s3::types::BucketLocationConstraint::from(env.aws_region.as_str()),
                        )
                        .build(),
                );
            }
            req.send()
                .await
                .with_context(|| format!("create bucket {bucket_name}"))?;
        }
    }

    // Versioning
    if spec.versioning {
        if dry_run {
            println!("[DRY RUN] enable versioning on {bucket_name}");
        } else {
            s3.put_bucket_versioning()
                .bucket(bucket_name)
                .versioning_configuration(
                    aws_sdk_s3::types::VersioningConfiguration::builder()
                        .status(aws_sdk_s3::types::BucketVersioningStatus::Enabled)
                        .build(),
                )
                .send()
                .await
                .context("put bucket versioning")?;
        }
    }

    // Encryption
    if !spec.encryption.is_empty() {
        if dry_run {
            println!("[DRY RUN] set encryption {} on {bucket_name}", spec.encryption);
        } else {
            s3.put_bucket_encryption()
                .bucket(bucket_name)
                .server_side_encryption_configuration(
                    aws_sdk_s3::types::ServerSideEncryptionConfiguration::builder()
                        .rules(
                            aws_sdk_s3::types::ServerSideEncryptionRule::builder()
                                .apply_server_side_encryption_by_default(
                                    aws_sdk_s3::types::ServerSideEncryptionByDefault::builder()
                                        .sse_algorithm(aws_sdk_s3::types::ServerSideEncryption::Aes256)
                                        .build()?,
                                )
                                .build(),
                        )
                        .build()?,
                )
                .send()
                .await
                .context("put bucket encryption")?;
        }
    }

    // Tags
    if dry_run {
        println!("[DRY RUN] tag s3 bucket {bucket_name}");
    } else {
        let tagset = aws_sdk_s3::types::Tagging::builder()
            .tag_set(
                aws_sdk_s3::types::Tag::builder()
                    .key(TAG_MANAGED_BY_KEY)
                    .value(TAG_MANAGED_BY_VALUE)
                    .build()?,
            )
            .tag_set(
                aws_sdk_s3::types::Tag::builder()
                    .key(TAG_ENV_KEY)
                    .value(&env.environment)
                    .build()?,
            )
            .build()?;
        let _ = s3
            .put_bucket_tagging()
            .bucket(bucket_name)
            .tagging(tagset)
            .send()
            .await;
    }

    Ok(())
}

async fn ensure_ecr_repo(
    ecr: &aws_sdk_ecr::Client,
    repo: &config::EcrRepository,
    env: &AwsEnv,
    dry_run: bool,
) -> Result<state::ResourceRef> {
    let described = ecr
        .describe_repositories()
        .repository_names(repo.name.clone())
        .send()
        .await;

    let (arn, uri) = match described {
        Ok(out) => {
            let r = out
                .repositories()
                .first()
                .context("missing repository in describe response")?;
            (
                r.repository_arn().unwrap_or_default().to_string(),
                r.repository_uri().unwrap_or_default().to_string(),
            )
        }
        Err(_) => {
            if dry_run {
                println!("[DRY RUN] create ecr repo {}", repo.name);
                (
                    format!("arn:aws:ecr:{}:{}:repository/{}", env.aws_region, env.account_id, repo.name),
                    format!("{}.dkr.ecr.{}.amazonaws.com/{}", env.account_id, env.aws_region, repo.name),
                )
            } else {
                let out = ecr
                    .create_repository()
                    .repository_name(repo.name.clone())
                    .image_scanning_configuration(
                        aws_sdk_ecr::types::ImageScanningConfiguration::builder()
                            .scan_on_push(repo.scan_on_push)
                            .build(),
                    )
                    .image_tag_mutability(
                        aws_sdk_ecr::types::ImageTagMutability::from(repo.image_tag_mutability.as_str()),
                    )
                    .tags(
                        aws_sdk_ecr::types::Tag::builder()
                            .key(TAG_MANAGED_BY_KEY)
                            .value(TAG_MANAGED_BY_VALUE)
                            .build()?,
                    )
                    .tags(
                        aws_sdk_ecr::types::Tag::builder()
                            .key(TAG_ENV_KEY)
                            .value(&env.environment)
                            .build()?,
                    )
                    .send()
                    .await
                    .with_context(|| format!("create ecr repo {}", repo.name))?;
                let r = out.repository().context("missing repository in create response")?;
                (
                    r.repository_arn().unwrap_or_default().to_string(),
                    r.repository_uri().unwrap_or_default().to_string(),
                )
            }
        }
    };

    Ok(state::ResourceRef {
        arn: Some(arn),
        uri: Some(uri),
        name: Some(repo.name.clone()),
        ..Default::default()
    })
}

async fn ensure_portfolio(
    sc: &aws_sdk_servicecatalog::Client,
    key: &str,
    spec: &config::PortfolioSpec,
    env: &AwsEnv,
    dry_run: bool,
) -> Result<state::ResourceRef> {
    let display_name = format!("{} ({})", spec.display_name, env.environment);

    // Find existing by display name
    let mut existing: Option<(String, String)> = None;
    let mut next = None;
    loop {
        let mut req = sc.list_portfolios();
        if let Some(token) = next.take() {
            req = req.page_token(token);
        }
        let out = req.send().await.context("list_portfolios")?;
        for p in out.portfolio_details() {
            if p.display_name().unwrap_or_default() == display_name {
                existing = Some((
                    p.id().unwrap_or_default().to_string(),
                    p.arn().unwrap_or_default().to_string(),
                ));
                break;
            }
        }
        if existing.is_some() {
            break;
        }
        match out.next_page_token() {
            Some(t) if !t.is_empty() => next = Some(t.to_string()),
            _ => break,
        }
    }

    let (id, arn) = if let Some((id, arn)) = existing {
        (id, arn)
    } else if dry_run {
        println!("[DRY RUN] create portfolio {key} ({display_name})");
        ("port-dryrun".to_string(), "arn:dryrun".to_string())
    } else {
        let mut tags: Vec<aws_sdk_servicecatalog::types::Tag> = Vec::new();
        tags.push(
            aws_sdk_servicecatalog::types::Tag::builder()
                .key(TAG_MANAGED_BY_KEY)
                .value(TAG_MANAGED_BY_VALUE)
                .build()?,
        );
        tags.push(
            aws_sdk_servicecatalog::types::Tag::builder()
                .key(TAG_ENV_KEY)
                .value(&env.environment)
                .build()?,
        );
        for (k, v) in &spec.tags {
            tags.push(aws_sdk_servicecatalog::types::Tag::builder().key(k).value(v).build()?);
        }

        let out = sc
            .create_portfolio()
            .display_name(display_name.clone())
            .description(spec.description.clone())
            .provider_name(spec.provider_name.clone())
            .set_tags(Some(tags))
            .send()
            .await
            .with_context(|| format!("create portfolio {key}"))?;
        let p = out
            .portfolio_detail()
            .context("missing portfolio detail")?;
        (
            p.id().unwrap_or_default().to_string(),
            p.arn().unwrap_or_default().to_string(),
        )
    };

    // Principals
    for principal in &spec.principals {
        if dry_run {
            println!("[DRY RUN] associate principal {principal} with portfolio {key}");
        } else {
            let _ = sc
                .associate_principal_with_portfolio()
                .portfolio_id(id.clone())
                .principal_arn(principal.replace("${account_id}", &env.account_id))
                .principal_type(aws_sdk_servicecatalog::types::PrincipalType::Iam)
                .send()
                .await;
        }
    }

    Ok(state::ResourceRef {
        id: Some(id),
        arn: Some(arn),
        name: Some(display_name),
        ..Default::default()
    })
}

async fn ensure_launch_role(
    iam: &aws_sdk_iam::Client,
    env: &AwsEnv,
    dry_run: bool,
) -> Result<state::ResourceRef> {
    let role_name = format!("scd-launch-role-{}", env.environment);

    let role = iam.get_role().role_name(&role_name).send().await;
    let arn = match role {
        Ok(out) => out
            .role()
            .map(|r| r.arn().to_string())
            .unwrap_or_default(),
        Err(_) => {
            if dry_run {
                println!("[DRY RUN] create iam role {role_name}");
                format!("arn:aws:iam::{}:role/{role_name}", env.account_id)
            } else {
                let trust = serde_json::json!({
                  "Version": "2012-10-17",
                  "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "servicecatalog.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                  }]
                })
                .to_string();

                let out = iam
                    .create_role()
                    .role_name(&role_name)
                    .assume_role_policy_document(trust)
                    .description(format!("Service Catalog launch role for {}", env.environment))
                    .tags(
                        aws_sdk_iam::types::Tag::builder()
                            .key(TAG_MANAGED_BY_KEY)
                            .value(TAG_MANAGED_BY_VALUE)
                            .build()?,
                    )
                    .tags(
                        aws_sdk_iam::types::Tag::builder()
                            .key(TAG_ENV_KEY)
                            .value(&env.environment)
                            .build()?,
                    )
                    .send()
                    .await
                    .context("create role")?;

                out.role()
                    .map(|r| r.arn().to_string())
                    .unwrap_or_default()
            }
        }
    };

    // Attach broad policies (MVP parity; tighten later)
    if dry_run {
        println!("[DRY RUN] attach policies to {role_name}");
    } else {
        let policies = [
            "arn:aws:iam::aws:policy/AWSCloudFormationFullAccess",
            "arn:aws:iam::aws:policy/AmazonS3FullAccess",
            "arn:aws:iam::aws:policy/AmazonEC2FullAccess",
            "arn:aws:iam::aws:policy/IAMFullAccess",
        ];
        for p in policies {
            let _ = iam
                .attach_role_policy()
                .role_name(&role_name)
                .policy_arn(p)
                .send()
                .await;
        }
    }

    Ok(state::ResourceRef {
        name: Some(role_name),
        arn: Some(arn),
        ..Default::default()
    })
}

async fn ensure_product(
    sc: &aws_sdk_servicecatalog::Client,
    s3: &aws_sdk_s3::Client,
    key: &str,
    _spec: &config::ProductSpec,
    bucket_name: &str,
    env: &AwsEnv,
    dry_run: bool,
) -> Result<state::ResourceRef> {
    let product_name = format!("{}-{}", key, env.environment);

    // Search existing products as admin
    let out = sc
        .search_products_as_admin()
        .filters(
            aws_sdk_servicecatalog::types::ProductViewFilterBy::FullTextSearch,
            vec![product_name.clone()],
        )
        .send()
        .await
        .context("search_products_as_admin")?;

    if let Some(pvd) = out
        .product_view_details()
        .iter()
        .find(|pvd| pvd.product_view_summary().and_then(|s| s.name()) == Some(product_name.as_str()))
    {
        let id = pvd
            .product_view_summary()
            .and_then(|s| s.product_id())
            .unwrap_or_default()
            .to_string();
        let arn = pvd.product_arn().unwrap_or_default().to_string();
        return Ok(state::ResourceRef {
            id: Some(id),
            arn: Some(arn),
            name: Some(product_name),
            ..Default::default()
        });
    }

    // Create placeholder template in S3
    let placeholder = r#"AWSTemplateFormatVersion: '2010-09-09'
Description: Placeholder template - will be replaced on first publish

Resources:
  PlaceholderWaitHandle:
    Type: AWS::CloudFormation::WaitConditionHandle

Outputs:
  Status:
    Description: Placeholder status
    Value: "Pending first publish"
"#;
    let s3_key = format!("_placeholders/{product_name}/placeholder.yaml");
    let template_url = format!(
        "https://{bucket_name}.s3.{}.amazonaws.com/{s3_key}",
        env.aws_region
    );

    if dry_run {
        println!("[DRY RUN] upload placeholder template s3://{bucket_name}/{s3_key}");
        println!("[DRY RUN] create servicecatalog product {product_name}");
        return Ok(state::ResourceRef {
            id: Some("prod-dryrun".to_string()),
            arn: Some("arn:dryrun".to_string()),
            name: Some(product_name),
            ..Default::default()
        });
    }

    s3.put_object()
        .bucket(bucket_name)
        .key(&s3_key)
        .body(aws_sdk_s3::primitives::ByteStream::from(placeholder.as_bytes().to_vec()))
        .content_type("application/x-yaml")
        .send()
        .await
        .context("put_object placeholder")?;

    let mut tags: Vec<aws_sdk_servicecatalog::types::Tag> = Vec::new();
    tags.push(
        aws_sdk_servicecatalog::types::Tag::builder()
            .key(TAG_MANAGED_BY_KEY)
            .value(TAG_MANAGED_BY_VALUE)
            .build()?,
    );
    tags.push(
        aws_sdk_servicecatalog::types::Tag::builder()
            .key(TAG_ENV_KEY)
            .value(&env.environment)
            .build()?,
    );
    tags.push(
        aws_sdk_servicecatalog::types::Tag::builder()
            .key("ProductKey")
            .value(key)
            .build()?,
    );

    let out = sc
        .create_product()
        .name(&product_name)
        .owner("Platform Team")
        .description(format!("Service Catalog product: {key}"))
        .product_type(aws_sdk_servicecatalog::types::ProductType::CloudFormationTemplate)
        .set_tags(Some(tags))
        .provisioning_artifact_parameters(
            aws_sdk_servicecatalog::types::ProvisioningArtifactProperties::builder()
                .name("v0.0.0-placeholder")
                .description("Placeholder - will be replaced on first publish")
                .r#type(aws_sdk_servicecatalog::types::ProvisioningArtifactType::CloudFormationTemplate)
                .info("LoadTemplateFromURL", template_url)
                .build(),
        )
        .send()
        .await
        .with_context(|| format!("create product {product_name}"))?;

    let pvd = out
        .product_view_detail()
        .context("missing product view detail")?;
    let id = pvd
        .product_view_summary()
        .and_then(|s| s.product_id())
        .unwrap_or_default()
        .to_string();
    let arn = pvd.product_arn().unwrap_or_default().to_string();

    Ok(state::ResourceRef {
        id: Some(id),
        arn: Some(arn),
        name: Some(product_name),
        ..Default::default()
    })
}

async fn ensure_product_in_portfolio(
    sc: &aws_sdk_servicecatalog::Client,
    product_id: &str,
    portfolio_id: &str,
    dry_run: bool,
) -> Result<()> {
    if dry_run {
        println!("[DRY RUN] associate product {product_id} with portfolio {portfolio_id}");
        return Ok(());
    }
    let _ = sc
        .associate_product_with_portfolio()
        .product_id(product_id)
        .portfolio_id(portfolio_id)
        .send()
        .await;
    Ok(())
}

async fn ensure_launch_constraint(
    sc: &aws_sdk_servicecatalog::Client,
    portfolio_id: &str,
    product_id: &str,
    role_arn: &str,
    product_name: &str,
    dry_run: bool,
) -> Result<()> {
    if dry_run {
        println!("[DRY RUN] create launch constraint for {product_name}");
        return Ok(());
    }

    // Best-effort: create constraint; ignore if it already exists.
    let params = serde_json::json!({ "RoleArn": role_arn }).to_string();
    let _ = sc
        .create_constraint()
        .portfolio_id(portfolio_id)
        .product_id(product_id)
        .r#type("LAUNCH")
        .parameters(params)
        .description(format!("Launch constraint for {product_name}"))
        .send()
        .await;
    Ok(())
}

