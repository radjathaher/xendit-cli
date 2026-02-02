use anyhow::{Context, Result};
use reqwest::blocking::{Client, RequestBuilder};
use reqwest::header::HeaderMap;
use serde_json::{Map, Value, json};

pub struct HttpClient {
    base_url: String,
    api_key: String,
    client: Client,
}

impl HttpClient {
    pub fn new(base_url: String, api_key: String) -> Result<Self> {
        let client = Client::builder()
            .user_agent("xendit-cli")
            .build()
            .context("build http client")?;
        Ok(Self {
            base_url,
            api_key,
            client,
        })
    }

    pub fn execute(
        &self,
        method: &str,
        path: &str,
        query: &[(String, String)],
        body: Option<Value>,
        raw: bool,
        pretty: bool,
    ) -> Result<(String, bool, u16)> {
        let url = format!("{}{}", self.base_url.trim_end_matches('/'), path);
        let method = method.parse().context("invalid http method")?;
        let mut req = self.client.request(method, url).basic_auth(&self.api_key, Some(""));
        req = apply_query(req, query);
        if let Some(value) = body {
            req = req.json(&value);
        }

        let resp = req.send().context("send request")?;
        let status = resp.status();
        let headers = resp.headers().clone();
        let text = resp.text().unwrap_or_default();
        let body_value = parse_body_value(&text);

        let output = if raw {
            let headers_value = headers_to_json(&headers);
            json!({
                "status": status.as_u16(),
                "headers": headers_value,
                "body": body_value,
            })
        } else {
            body_value
        };

        let rendered = if pretty {
            serde_json::to_string_pretty(&output)?
        } else {
            serde_json::to_string(&output)?
        };

        Ok((rendered, status.is_success(), status.as_u16()))
    }
}

fn apply_query(req: RequestBuilder, query: &[(String, String)]) -> RequestBuilder {
    if query.is_empty() {
        return req;
    }
    req.query(&query)
}

fn parse_body_value(text: &str) -> Value {
    if text.trim().is_empty() {
        return Value::Null;
    }
    serde_json::from_str(text).unwrap_or_else(|_| Value::String(text.to_string()))
}

fn headers_to_json(headers: &HeaderMap) -> Value {
    let mut map = Map::new();
    for (key, value) in headers.iter() {
        let val = value.to_str().unwrap_or("").to_string();
        map.insert(key.to_string(), Value::String(val));
    }
    Value::Object(map)
}
