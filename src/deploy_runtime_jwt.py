import io, json, time, uuid, zipfile
import boto3

def make_zip_bytes(py_file_path: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(py_file_path, arcname="runtime_agent_app.py")
    return buf.getvalue()

def main():
    with open("config.json", "r") as f:
        cfg = json.load(f)

    region = cfg["region"]
    bucket = cfg["runtime_code_s3_bucket"]
    prefix = cfg["runtime_code_s3_prefix"].rstrip("/")
    role_arn = cfg["runtime_exec_role_arn"]
    network_mode = cfg.get("runtime_network_mode", "PUBLIC")

    discovery_url = cfg["jwt_discovery_url"]
    allowed_aud = cfg["jwt_allowed_audiences"]

    runtime_name = f"{cfg.get('runtime_name_prefix','policy-agent-runtime')}-jwt-{int(time.time())}"
    endpoint_name = cfg.get("runtime_endpoint_name", "policy-agent-endpoint")

    # Package agent code
    s3_key = f"{prefix}/runtime_agent_app_jwt_{int(time.time())}.zip"
    zip_bytes = make_zip_bytes("src/runtime_agent_app.py")
    s3 = boto3.client("s3", region_name=region)
    s3.put_object(Bucket=bucket, Key=s3_key, Body=zip_bytes)

    control = boto3.client("bedrock-agentcore-control", region_name=region)

    # Create runtime with JWT inbound auth (AgentCore Identity)
    runtime_resp = control.create_agent_runtime(
        agentRuntimeName=runtime_name,
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {"s3": {"bucket": bucket, "prefix": s3_key}},
                "runtime": "PYTHON_3_11",
                "entryPoint": ["runtime_agent_app:handler"],
            }
        },
        roleArn=role_arn,
        networkConfiguration={"networkMode": network_mode},
        protocolConfiguration={"serverProtocol": "HTTP"},
        # Inbound Auth (JWT) configuration
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": discovery_url,
                "allowedClients": allowed_aud
            }
        },
        description="Runtime with JWT inbound auth for identity E2E",
        tags={"purpose": "agentcore-policy-e2e", "auth": "jwt"}
    )

    agent_runtime_id = runtime_resp["agentRuntimeId"]
    agent_runtime_arn = runtime_resp["agentRuntimeArn"]

    endpoint_resp = control.create_agent_runtime_endpoint(
        agentRuntimeId=agent_runtime_id,
        name=endpoint_name,
        clientToken=str(uuid.uuid4()).replace("-", "")
    )

    out = {
        "region": region,
        "agentRuntimeId": endpoint_resp["agentRuntimeId"],
        "agentRuntimeArn": endpoint_resp["agentRuntimeArn"],
        "agentRuntimeEndpointArn": endpoint_resp["agentRuntimeEndpointArn"],
        "endpointName": endpoint_resp["endpointName"],
        "status": endpoint_resp["status"],
        "runtimeArtifactS3": {"bucket": bucket, "key": s3_key},
    }

    with open("config_runtime_agent_jwt.json", "w") as f:
        json.dump(out, f, indent=2)

    print("✅ JWT Runtime + endpoint created.")
    print("agentRuntimeArn:", out["agentRuntimeArn"])
    print("agentRuntimeEndpointArn:", out["agentRuntimeEndpointArn"])
    print("qualifier (endpointName):", out["endpointName"])
    print("Wrote config_runtime_agent_jwt.json")

if __name__ == "__main__":
    main()
