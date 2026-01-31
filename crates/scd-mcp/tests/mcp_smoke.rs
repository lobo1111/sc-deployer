use rmcp::{
    model::CallToolRequestParams,
    service::RoleClient,
    transport::TokioChildProcess,
    ServiceExt,
};
use tokio::process::Command;

#[tokio::test]
async fn mcp_server_lists_tools_and_runs_version() {
    let server_path = env!("CARGO_BIN_EXE_scd-mcp");

    let mut cmd = Command::new(server_path);
    // Ensure we can find `scd` even if the test runner has a minimal PATH.
    if let Ok(home) = std::env::var("HOME") {
        if let Ok(path) = std::env::var("PATH") {
            cmd.env("PATH", format!("{path}:{home}/.local/bin"));
        }
    }

    let transport = TokioChildProcess::new(cmd).expect("spawn scd-mcp");
    let service = <() as ServiceExt<RoleClient>>::serve((), transport)
        .await
        .expect("connect to scd-mcp");

    let tools = service.list_tools(Default::default()).await.expect("list tools");
    assert!(
        tools.tools.iter().any(|t| t.name == "scd_version"),
        "expected scd_version tool, got: {:#?}",
        tools.tools.iter().map(|t| &t.name).collect::<Vec<_>>()
    );

    let res = service
        .call_tool(CallToolRequestParams {
            meta: None,
            name: "scd_version".into(),
            arguments: None,
            task: None,
        })
        .await
        .expect("call scd_version");

    let dbg = format!("{res:#?}");
    assert!(
        dbg.contains("scd") || dbg.contains("exit_code"),
        "unexpected response: {dbg}"
    );

    service.cancel().await.expect("cancel");
}

