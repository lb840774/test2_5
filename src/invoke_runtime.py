import json
import uuid
import boto3

def read_streaming_body(streaming_body) -> str:
    return streaming_body.read().decode("utf-8")

def main():
    with open("config_runtime_agent.json", "r") as f:
        rt = json.load(f)

    region = rt["region"]
    agentcore = boto3.client("bedrock-agentcore", region_name=region)

    tool_full_name = f"{rt['target_name']}___{rt['tool_name']}"

    # Try one allowed and one denied to prove policy still applies from runtime
    for amt in (500, 2000):
        payload = {
            "amount": amt,
            "gateway_url": rt["gateway_url"],
            "tool_full_name": tool_full_name
        }

        resp = agentcore.invoke_agent_runtime(
            agentRuntimeArn=rt["agentRuntimeArn"],
            qualifier=rt["endpointName"],
            runtimeSessionId=str(uuid.uuid4()),
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(payload).encode("utf-8")
        )

        body = read_streaming_body(resp["response"])
        print("\n==============================")
        print("Invoked runtime with amount =", amt)
        print("StatusCode:", resp.get("statusCode"))
        print(body[:2500])

if __name__ == "__main__":
    main()
