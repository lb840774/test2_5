import json
import uuid
import boto3

from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

def read_streaming_body(streaming_body) -> str:
    # Response is a StreamingBody; read it fully for simplicity
    return streaming_body.read().decode("utf-8")

def main():
    with open("config_runtime.json", "r") as f:
        gw = json.load(f)

    with open("config_runtime_agent.json", "r") as f:
        rt = json.load(f)

    region = rt["region"]

    # 1) Get the same Cognito token we used for gateway tests
    gateway_client = GatewayClient(region_name=region)
    access_token = gateway_client.get_access_token_for_cognito(gw["client_info"])

    tool_full_name = f"{rt['target_name']}___{rt['tool_name']}"

    # 2) Invoke runtime
    agentcore = boto3.client("bedrock-agentcore", region_name=region)

    payload = {
        "amount": 500,
        "gateway_url": rt["gateway_url"],
        "access_token": access_token,
        "tool_full_name": tool_full_name
    }

    resp = agentcore.invoke_agent_runtime(
        agentRuntimeArn=rt["agentRuntimeArn"],
        qualifier=rt["endpointName"],            # target specific endpoint
        runtimeSessionId=str(uuid.uuid4()),      # new session
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload).encode("utf-8")
    )

    body = read_streaming_body(resp["response"])
    print("StatusCode:", resp.get("statusCode"))
    print(body)

if __name__ == "__main__":
    main()
