mod command_tree;
mod http;

use anyhow::{Context, Result, anyhow};
use clap::{Arg, ArgAction, Command};
use command_tree::{CommandTree, Operation, ParamDef};
use serde_json::Value;
use std::{env, fs, io::Write, path::Path};

fn main() {
    if let Err(err) = run() {
        eprintln!("error: {err}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let tree = command_tree::load_command_tree();
    let cli = build_cli(&tree);
    let matches = cli.get_matches();

    if let Some(matches) = matches.subcommand_matches("list") {
        return handle_list(&tree, matches);
    }
    if let Some(matches) = matches.subcommand_matches("describe") {
        return handle_describe(&tree, matches);
    }
    if let Some(matches) = matches.subcommand_matches("tree") {
        return handle_tree(&tree, matches);
    }

    let base_url = matches
        .get_one::<String>("base_url")
        .cloned()
        .or_else(|| env::var("XENDIT_API_URL").ok())
        .unwrap_or_else(|| tree.base_url.clone());

    let api_key = matches
        .get_one::<String>("api_key")
        .cloned()
        .or_else(|| env::var("XENDIT_API_KEY").ok())
        .context("XENDIT_API_KEY missing")?;

    let pretty = matches.get_flag("pretty");
    let raw = matches.get_flag("raw");

    let (res_name, res_matches) = matches
        .subcommand()
        .ok_or_else(|| anyhow!("resource required"))?;
    let (op_name, op_matches) = res_matches
        .subcommand()
        .ok_or_else(|| anyhow!("operation required"))?;

    let op = find_op(&tree, res_name, op_name)
        .ok_or_else(|| anyhow!("unknown command {res_name} {op_name}"))?;

    let (path, query) = build_request_parts(op, op_matches)?;
    let body = if op.has_body {
        parse_body_arg(op_matches)?
    } else {
        None
    };

    let client = http::HttpClient::new(base_url, api_key)?;
    let (output, ok, status) = client.execute(&op.method, &path, &query, body, raw, pretty)?;

    write_stdout_line(&output)?;
    if !ok {
        return Err(anyhow!("http {}", status));
    }
    Ok(())
}

fn build_cli(tree: &CommandTree) -> Command {
    let mut cmd = Command::new("xendit")
        .about("Xendit CLI (auto-generated)")
        .subcommand_required(true)
        .arg_required_else_help(true)
        .arg(
            Arg::new("pretty")
                .long("pretty")
                .global(true)
                .action(ArgAction::SetTrue)
                .help("Pretty-print JSON output"),
        )
        .arg(
            Arg::new("raw")
                .long("raw")
                .global(true)
                .action(ArgAction::SetTrue)
                .help("Include status and headers"),
        )
        .arg(
            Arg::new("base_url")
                .long("base-url")
                .global(true)
                .value_name("URL")
                .help("Override base API URL"),
        )
        .arg(
            Arg::new("api_key")
                .long("api-key")
                .global(true)
                .value_name("KEY")
                .help("Override XENDIT_API_KEY"),
        );

    cmd = cmd.subcommand(
        Command::new("list")
            .about("List resources and operations")
            .arg(
                Arg::new("json")
                    .long("json")
                    .action(ArgAction::SetTrue)
                    .help("Emit machine-readable JSON"),
            ),
    );

    cmd = cmd.subcommand(
        Command::new("describe")
            .about("Describe a specific operation")
            .arg(Arg::new("resource").required(true))
            .arg(Arg::new("op").required(true))
            .arg(
                Arg::new("json")
                    .long("json")
                    .action(ArgAction::SetTrue)
                    .help("Emit machine-readable JSON"),
            ),
    );

    cmd = cmd.subcommand(
        Command::new("tree")
            .about("Show full command tree")
            .arg(
                Arg::new("json")
                    .long("json")
                    .action(ArgAction::SetTrue)
                    .help("Emit machine-readable JSON"),
            ),
    );

    for resource in &tree.resources {
        let mut res_cmd = Command::new(resource.name.clone())
            .about(resource.name.clone())
            .subcommand_required(true)
            .arg_required_else_help(true);

        for op in &resource.ops {
            let mut op_cmd = Command::new(op.name.clone()).about(op.path.clone());
            for param in &op.params {
                op_cmd = op_cmd.arg(build_param_arg(param));
            }
            if op.has_body {
                op_cmd = op_cmd.arg(
                    Arg::new("body")
                        .long("body")
                        .value_name("JSON")
                        .help("Request body JSON (or @file.json)"),
                );
            }
            res_cmd = res_cmd.subcommand(op_cmd);
        }
        cmd = cmd.subcommand(res_cmd);
    }

    cmd
}

fn handle_list(tree: &CommandTree, matches: &clap::ArgMatches) -> Result<()> {
    if matches.get_flag("json") {
        let out: Vec<_> = tree
            .resources
            .iter()
            .map(|res| {
                let ops: Vec<String> = res.ops.iter().map(|op| op.name.clone()).collect();
                serde_json::json!({"resource": res.name, "ops": ops})
            })
            .collect();
        write_stdout_line(&serde_json::to_string_pretty(&out)?)?;
        return Ok(());
    }

    for res in &tree.resources {
        write_stdout_line(&res.name)?;
        for op in &res.ops {
            write_stdout_line(&format!("  {}", op.name))?;
        }
    }
    Ok(())
}

fn handle_describe(tree: &CommandTree, matches: &clap::ArgMatches) -> Result<()> {
    let resource = matches
        .get_one::<String>("resource")
        .ok_or_else(|| anyhow!("resource required"))?;
    let op_name = matches
        .get_one::<String>("op")
        .ok_or_else(|| anyhow!("operation required"))?;

    let op = find_op(tree, resource, op_name)
        .ok_or_else(|| anyhow!("unknown command {resource} {op_name}"))?;

    if matches.get_flag("json") {
        write_stdout_line(&serde_json::to_string_pretty(op)?)?;
        return Ok(());
    }

    write_stdout_line(&format!("{} {}", resource, op.name))?;
    write_stdout_line(&format!("  method: {}", op.method))?;
    write_stdout_line(&format!("  path: {}", op.path))?;
    if let Some(desc) = &op.description {
        if !desc.trim().is_empty() {
            write_stdout_line(&format!("  description: {}", desc.trim()))?;
        }
    }
    if !op.params.is_empty() {
        write_stdout_line("  params:")?;
        for param in &op.params {
            let req = if param.required { "required" } else { "optional" };
            write_stdout_line(&format!("    --{}  {} ({})", param.flag, param.location, req))?;
        }
    }
    if op.has_body {
        write_stdout_line("  body: --body JSON or @file.json")?;
    }
    Ok(())
}

fn handle_tree(tree: &CommandTree, matches: &clap::ArgMatches) -> Result<()> {
    if matches.get_flag("json") {
        write_stdout_line(&serde_json::to_string_pretty(tree)?)?;
        return Ok(());
    }
    write_stdout_line("Run with --json for machine-readable output.")?;
    Ok(())
}

fn write_stdout_line(value: &str) -> Result<()> {
    let mut out = std::io::stdout().lock();
    if let Err(err) = out.write_all(value.as_bytes()) {
        if err.kind() == std::io::ErrorKind::BrokenPipe {
            std::process::exit(0);
        }
        return Err(err.into());
    }
    if let Err(err) = out.write_all(b"\n") {
        if err.kind() == std::io::ErrorKind::BrokenPipe {
            std::process::exit(0);
        }
        return Err(err.into());
    }
    Ok(())
}

fn build_param_arg(param: &ParamDef) -> Arg {
    Arg::new(param.name.clone())
        .long(param.flag.clone())
        .value_name("VALUE")
        .required(param.required && param.location == "path")
}

fn find_op<'a>(tree: &'a CommandTree, res: &str, op: &str) -> Option<&'a Operation> {
    tree.resources
        .iter()
        .find(|r| r.name == res)
        .and_then(|r| r.ops.iter().find(|o| o.name == op))
}

fn build_request_parts(
    op: &Operation,
    matches: &clap::ArgMatches,
) -> Result<(String, Vec<(String, String)>)> {
    let mut path = op.path.clone();
    let mut query = Vec::new();

    for param in &op.params {
        let value = matches.get_one::<String>(&param.name).map(String::as_str);
        if param.location == "path" {
            let value = value.ok_or_else(|| anyhow!("missing required argument --{}", param.flag))?;
            path = replace_path_param(&path, &param.name, value);
        } else if param.location == "query" {
            if let Some(value) = value {
                query.push((param.name.clone(), value.to_string()));
            }
        }
    }

    if path.contains('{') {
        return Err(anyhow!("unresolved path params: {path}"));
    }

    Ok((path, query))
}

fn replace_path_param(path: &str, name: &str, value: &str) -> String {
    let mut out = path.to_string();
    for placeholder in [
        format!("{{{name}}}"),
        format!(":{name}"),
        format!("{{{{{name}}}}}"),
    ] {
        out = out.replace(&placeholder, value);
    }
    out
}

fn parse_body_arg(matches: &clap::ArgMatches) -> Result<Option<Value>> {
    let Some(value) = matches.get_one::<String>("body") else {
        return Ok(None);
    };

    let raw = if let Some(path) = value.strip_prefix('@') {
        let body_path = Path::new(path);
        fs::read_to_string(body_path).context("read body file")?
    } else {
        value.to_string()
    };

    let parsed = serde_json::from_str(&raw).context("invalid JSON body")?;
    Ok(Some(parsed))
}
