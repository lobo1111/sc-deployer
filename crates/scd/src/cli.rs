use anyhow::Result;
use clap::{CommandFactory, Parser, Subcommand};

use crate::aws;
use crate::deploy;
use crate::manage;
use crate::project;

#[derive(Debug, Parser)]
#[command(name = "scd", version, about = "Service Catalog Deployer (Rust)")]
pub struct RootCmd {
    /// Override project root (directory that contains `.deployer/`)
    #[arg(long, global = true)]
    pub project: Option<std::path::PathBuf>,

    #[command(subcommand)]
    pub cmd: Command,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    /// Initialize a new project directory (creates `.deployer/`, `products/`, `.gitignore`, and runs `git init`)
    Init {
        /// Project directory name (created under the current directory)
        #[arg(long)]
        name: String,

        /// Create sample product(s) and YAML
        #[arg(long)]
        sample: bool,

        /// Use interactive prompts (not implemented yet)
        #[arg(long)]
        interactive: bool,
    },

    /// Describe discovered project layout
    ProjectStatus,

    /// Configure/verify AWS connectivity for an environment
    Connect {
        #[arg(short = 'e', long)]
        environment: String,

        #[arg(long)]
        aws_profile: Option<String>,

        #[arg(long)]
        region: Option<String>,

        #[arg(long)]
        account_id: Option<String>,

        /// Trigger `aws sso login --profile <aws_profile>` before verifying
        #[arg(long)]
        sso_login: bool,
    },

    /// Reconcile local YAML desired state into AWS (idempotent)
    Sync {
        #[arg(short = 'e', long)]
        environment: String,

        #[arg(long)]
        dry_run: bool,
    },

    /// Tear down everything managed by scd in the target environment
    Destroy {
        #[arg(short = 'e', long)]
        environment: String,

        #[arg(long)]
        dry_run: bool,

        #[arg(long)]
        force: bool,
    },

    /// Deploy lifecycle commands
    Deploy {
        #[command(subcommand)]
        cmd: DeployCommand,
    },

    /// Manage configured environments (profiles)
    Profiles {
        #[command(subcommand)]
        cmd: ProfilesCommand,
    },

    /// Manage products and `.deployer/catalog.yaml`
    Products {
        #[command(subcommand)]
        cmd: ProductsCommand,
    },

    /// Generate shell completion scripts
    Completion {
        #[arg(value_enum)]
        shell: clap_complete::Shell,
    },
}

#[derive(Debug, Subcommand)]
pub enum ProfilesCommand {
    /// List configured environments in `.deployer/profiles.yaml`
    List,

    /// Set (write) an environment profile
    Set {
        #[arg(short = 'e', long)]
        environment: String,

        #[arg(long)]
        aws_profile: String,

        #[arg(long)]
        region: String,

        #[arg(long)]
        account_id: String,

        /// Verify credentials via STS after writing
        #[arg(long)]
        verify: bool,

        /// Trigger `aws sso login --profile <aws_profile>` before verifying
        #[arg(long)]
        sso_login: bool,
    },

    /// Verify AWS identity (STS GetCallerIdentity) for an environment
    Whoami {
        #[arg(short = 'e', long)]
        environment: String,
    },
}

#[derive(Debug, Subcommand)]
pub enum ProductsCommand {
    /// List products from `.deployer/catalog.yaml`
    List,

    /// Add a new product (creates files and updates `.deployer/catalog.yaml`)
    Add {
        #[arg(long)]
        name: String,

        /// Directory under `products/` (defaults to `--name`)
        #[arg(long)]
        path: Option<String>,

        #[arg(long)]
        portfolio: Option<String>,

        #[arg(long)]
        description: Option<String>,

        #[arg(long = "dependency")]
        dependencies: Vec<String>,

        #[arg(long = "output")]
        outputs: Vec<String>,

        /// Mapping in form `ParamName=dep.output`
        #[arg(long = "param-mapping")]
        mappings: Vec<String>,
    },

    /// Print dependency graph
    Graph,
}

#[derive(Debug, Subcommand)]
pub enum DeployCommand {
    /// Validate `.deployer/catalog.yaml` (deps + mappings) and bootstrap state presence
    Validate {
        #[arg(short = 'e', long)]
        environment: String,
    },

    /// Show deployment order (topological)
    Plan {
        #[arg(short = 'e', long)]
        environment: String,

        /// Specific product(s) to include
        #[arg(short = 'p', long = "product")]
        products: Vec<String>,
    },

    /// Publish templates to S3 and create Service Catalog provisioning artifacts
    Publish {
        #[arg(short = 'e', long)]
        environment: String,

        /// Specific product(s) to publish (defaults to all)
        #[arg(short = 'p', long = "product")]
        products: Vec<String>,

        #[arg(long)]
        dry_run: bool,

        /// Publish even if unchanged (change detection is minimal in this MVP)
        #[arg(long)]
        force: bool,
    },

    /// Apply (provision/update) published versions
    Apply {
        #[arg(short = 'e', long)]
        environment: String,

        /// Specific product(s) to apply (defaults to all)
        #[arg(short = 'p', long = "product")]
        products: Vec<String>,

        #[arg(long)]
        dry_run: bool,
    },

    /// Show deploy status
    Status {
        #[arg(short = 'e', long)]
        environment: String,
    },

    /// Terminate provisioned products
    Terminate {
        #[arg(short = 'e', long)]
        environment: String,

        /// Specific product(s) to terminate (defaults to all provisioned)
        #[arg(short = 'p', long = "product")]
        products: Vec<String>,

        #[arg(long)]
        dry_run: bool,

        #[arg(long)]
        force: bool,
    },
}

pub async fn run(root: RootCmd) -> Result<()> {
    match root.cmd {
        Command::Init {
            name,
            sample,
            interactive,
        } => {
            if interactive {
                eprintln!("Note: --interactive is not implemented yet; continuing non-interactively.");
            }
            let dir = project::project_dir_from_name(&name)?;
            let layout = project::init_project(&dir, sample)?;
            println!("Initialized project: {}", layout.root.display());
            println!("  - {}", layout.deployer_dir().display());
            println!("  - {}", layout.products_dir().display());
            Ok(())
        }
        Command::ProjectStatus => {
            let layout = project::load_layout(root.project)?;
            println!("Project root: {}", layout.root.display());
            println!("  profiles:  {}", layout.profiles_yaml().display());
            println!("  bootstrap: {}", layout.bootstrap_yaml().display());
            println!("  catalog:   {}", layout.catalog_yaml().display());
            println!("  products:  {}", layout.products_dir().display());
            Ok(())
        }
        Command::Connect {
            environment,
            aws_profile,
            region,
            account_id,
            sso_login,
        } => {
            let layout = project::load_layout(root.project)?;
            aws::connect(
                &layout,
                environment,
                aws_profile,
                region,
                account_id,
                sso_login,
            )
            .await?;
            println!("AWS environment configured.");
            Ok(())
        }
        Command::Sync {
            environment,
            dry_run,
        } => {
            let layout = project::load_layout(root.project)?;
            aws::sync(&layout, environment, dry_run).await?;
            println!("Sync complete.");
            Ok(())
        }
        Command::Destroy {
            environment,
            dry_run,
            force,
        } => {
            let layout = project::load_layout(root.project)?;
            deploy::destroy(&layout, environment, dry_run, force).await?;
            println!("Destroy complete.");
            Ok(())
        }

        Command::Deploy { cmd } => {
            let layout = project::load_layout(root.project)?;
            match cmd {
                DeployCommand::Validate { environment } => {
                    deploy::validate(&layout, environment).await?;
                    println!("Validation passed.");
                    Ok(())
                }
                DeployCommand::Plan {
                    environment,
                    products,
                } => {
                    deploy::plan(&layout, environment, products).await?;
                    Ok(())
                }
                DeployCommand::Publish {
                    environment,
                    products,
                    dry_run,
                    force,
                } => {
                    deploy::publish(&layout, environment, products, dry_run, force).await?;
                    println!("Publish complete.");
                    Ok(())
                }
                DeployCommand::Apply {
                    environment,
                    products,
                    dry_run,
                } => {
                    deploy::apply(&layout, environment, products, dry_run).await?;
                    println!("Apply complete.");
                    Ok(())
                }
                DeployCommand::Status { environment } => {
                    deploy::status(&layout, environment).await?;
                    Ok(())
                }
                DeployCommand::Terminate {
                    environment,
                    products,
                    dry_run,
                    force,
                } => {
                    deploy::terminate(&layout, environment, products, dry_run, force).await?;
                    println!("Terminate complete.");
                    Ok(())
                }
            }
        }

        Command::Profiles { cmd } => {
            let layout = project::load_layout(root.project)?;
            match cmd {
                ProfilesCommand::List => manage::profiles_list(&layout),
                ProfilesCommand::Set {
                    environment,
                    aws_profile,
                    region,
                    account_id,
                    verify,
                    sso_login,
                } => {
                    manage::profiles_set(
                        &layout,
                        environment,
                        aws_profile,
                        region,
                        account_id,
                        verify,
                        sso_login,
                    )
                    .await?;
                    println!("Profile saved.");
                    Ok(())
                }
                ProfilesCommand::Whoami { environment } => {
                    manage::profiles_whoami(&layout, environment).await?;
                    println!("OK");
                    Ok(())
                }
            }
        }

        Command::Products { cmd } => {
            let layout = project::load_layout(root.project)?;
            match cmd {
                ProductsCommand::List => manage::products_list(&layout),
                ProductsCommand::Add {
                    name,
                    path,
                    portfolio,
                    description,
                    dependencies,
                    outputs,
                    mappings,
                } => {
                    manage::products_add(
                        &layout,
                        name,
                        path,
                        portfolio,
                        description,
                        dependencies,
                        outputs,
                        mappings,
                    )?;
                    println!("Product added.");
                    Ok(())
                }
                ProductsCommand::Graph => manage::products_graph(&layout),
            }
        }

        Command::Completion { shell } => {
            let mut cmd = RootCmd::command();
            clap_complete::generate(shell, &mut cmd, "scd", &mut std::io::stdout());
            Ok(())
        }
    }
}

