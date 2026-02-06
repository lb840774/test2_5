import io
import os
import json
import time
import uuid
import zipfile
import boto3

def make_zip_bytes(py_file_path: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(py_file_path, arcname="runtime_agent_app.py")
    return buf.getvalue()

def main():
    with open("config.json", "r") as f:
        cfg = json.load(f)

    with open("config_runtime.json", "r") as f:
        gw = json.load(f)

    region = cfg["region"]
    runtime_role_arn = cfg["runtime_exec_role_arn"]
    bucket = cfg["runtime_code_s3_bucket"]
    prefix = cfg["runtime_code_s3_prefix"].rstrip("/")
    runtime_name = f"{cfg.get('runtime_name_prefix','policy-agent-runtime')}-{int(time.time())}"
    endpoint_name = cfg.get("runtime_endpoint_name", "policy-agent-endpoint")
    network_mode = cfg.get("runtime_network_mode", "PUBLIC")

    s3_key = f"{prefix}/runtime_agent_app_{int(time.time())}.zip"

    # 1) Zip + upload agent code
    zip_bytes = make_zip_bytes("src/runtime_agent_app.py")
    s3 = boto3.client("s3", region_name=region)
    s3.put_object(Bucket=bucket, Key=s3_key, Body=zip_bytes)

    # 2) Create runtime
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    runtime_resp = control.create_agent_runtime(
        agentRuntimeName=runtime_name,
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {"s3": {"bucket": bucket, "prefix": s3_key}},
                "runtime": "PYTHON_3_11",
                # entryPoint is a list; module:function style works for code deployments
                "entryPoint": ["runtime_agent_app:handler"],
            }
        },
        roleArn=runtime_role_arn,
        networkConfiguration={
            "networkMode": network_mode
        },
        protocolConfiguration={"serverProtocol": "HTTP"},
        description="Policy E2E runtime that calls AgentCore Gateway tool via MCP",
        tags={"purpose": "agentcore-policy-e2e"}
    )

    agent_runtime_id = runtime_resp["agentRuntimeId"]
    agent_runtime_arn = runtime_resp["agentRuntimeArn"]

    # 3) Create endpoint
    endpoint_resp = control.create_agent_runtime_endpoint(
        agentRuntimeId=agent_runtime_id,
        name=endpoint_name,
        clientToken=str(uuid.uuid4()).replace("-", "") + str(uuid.uuid4()).replace("-", "")
    )

    out = {
        "region": region,
        "agentRuntimeId": endpoint_resp["agentRuntimeId"],
        "agentRuntimeArn": endpoint_resp["agentRuntimeArn"],
        "agentRuntimeEndpointArn": endpoint_resp["agentRuntimeEndpointArn"],
        "endpointName": endpoint_resp["endpointName"],
        "status": endpoint_resp["status"],
        "runtimeArtifactS3": {"bucket": bucket, "key": s3_key},
        # helpful to carry gateway details forward
        "gateway_url": gw["gateway_url"],
        "target_name": gw["target_name"],
        "tool_name": gw["tool_name"]
    }

    with open("config_runtime_agent.json", "w") as f:
        json.dump(out, f, indent=2)

    print("✅ Runtime + endpoint created.")
    print(f"agentRuntimeArn: {out['agentRuntimeArn']}")
    print(f"agentRuntimeEndpointArn: {out['agentRuntimeEndpointArn']}")
    print(f"qualifier (endpointName): {out['endpointName']}")
    print("Wrote config_runtime_agent.json")

if __name__ == "__main__":
    main()
