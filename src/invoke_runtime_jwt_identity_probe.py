import os, json, uuid, urllib.parse
import requests
import boto3

def get_valid_token(region: str, client_id: str, username: str, password: str) -> str:
    c = boto3.client("cognito-idp", region_name=region)
    r = c.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    return r["AuthenticationResult"]["AccessToken"]

def denied(code, body: str):
    body_l = (body or "").lower()
    return code in (401, 403) or any(k in body_l for k in ["unauthorized","forbidden","invalid token","not authorized"])

def main():
    # Read runtime identifiers from file created by deploy_runtime_jwt.py
    with open("config.json","r") as f:
        cfg = json.load(f)
    with open("config_runtime_agent_jwt.json","r") as f:
        rt = json.load(f)

    region = cfg["region"]
    client_id = os.getenv("COGNITO_CLIENT_ID")
    username = os.getenv("COGNITO_USERNAME")
    password = os.getenv("COGNITO_PASSWORD")
    session_id = cfg.get("runtime_session_id") or f"identity-e2e-{uuid.uuid4()}"

    if not all([client_id, username, password]):
        raise SystemExit("Missing env vars: COGNITO_CLIENT_ID, COGNITO_USERNAME, COGNITO_PASSWORD")

    # Build the official runtime invoke URL for JWT bearer invocation
    # Service endpoint + URL-encoded agentRuntimeArn + invocations + qualifier
    agent_arn_enc = urllib.parse.quote(rt["agentRuntimeArn"], safe="")
    qualifier = urllib.parse.quote(rt["endpointName"], safe="")
    invoke_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{agent_arn_enc}/invocations?qualifier={qualifier}"

    def call(token=None):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = {"input": {"text": "identity e2e probe"}}

        resp = requests.post(invoke_url, headers=headers, data=json.dumps(payload), timeout=45)
        return resp.status_code, resp.text[:800]

    valid = get_valid_token(region, client_id, username, password)
    bad = "eyJ.invalid.token"

    c1, b1 = call(None)
    c2, b2 = call(bad)
    c3, b3 = call(valid)

    report = {
        "invoke_url_built": invoke_url[:120] + "...",
        "no_token_denied": denied(c1,b1),
        "bad_token_denied": denied(c2,b2),
        "valid_token_allowed": (200 <= c3 < 300),
        "details": {
            "no_token": {"code": c1, "body": b1},
            "bad_token": {"code": c2, "body": b2},
            "valid": {"code": c3, "body": b3},
        }
    }
    print(json.dumps(report, indent=2))

    if not all([report["no_token_denied"], report["bad_token_denied"], report["valid_token_allowed"]]):
        raise SystemExit("E2E identity checks failed")

if __name__ == "__main__":
    main()
