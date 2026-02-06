"""
Test script for policy enforcement.

Run:
  python src/test_policy.py

Reads:
  config_runtime.json

What it proves:
- Lambda tool is permissive, but Gateway+Policy denies disallowed input in ENFORCE mode.
"""

import json
import uuid
import requests

from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient


def mcp_call(gateway_url: str, token: str, method: str, params=None):
    payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method}
    if params is not None:
        payload["params"] = params
    r = requests.post(
        gateway_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json=payload,
        timeout=30,
    )
    return r.status_code, r.json()


def main():
    with open("config_runtime.json", "r") as f:
        cfg = json.load(f)

    gateway_url = cfg["gateway_url"]
    region = cfg["region"]
    refund_limit = cfg["refund_limit"]
    target_name = cfg["target_name"]
    tool_name = cfg["tool_name"]

    # Get access token from Cognito (created during setup) – doc pattern
    print("Getting access token...")
    gateway_client = GatewayClient(region_name=region)
    token = gateway_client.get_access_token_for_cognito(cfg["client_info"])
    print("✓ Token obtained\n")

    tool_full_name = f"{target_name}___{tool_name}"

    # Optional: list tools
    print("tools/list:")
    code, out = mcp_call(gateway_url, token, "tools/list")
    print("HTTP", code)
    print(json.dumps(out, indent=2)[:2500])
    print()

    # Test 1: allowed
    allowed_amount = 500
    print(f"Test 1: Refund ${allowed_amount} (Expected: ALLOW)")
    code, out = mcp_call(gateway_url, token, "tools/call", {
        "name": tool_full_name,
        "arguments": {"amount": allowed_amount}
    })
    print("HTTP", code)
    print(json.dumps(out, indent=2)[:2500])
    print()

    # Test 2: denied
    denied_amount = 2000
    print(f"Test 2: Refund ${denied_amount} (Expected: DENY)")
    code, out = mcp_call(gateway_url, token, "tools/call", {
        "name": tool_full_name,
        "arguments": {"amount": denied_amount}
    })
    print("HTTP", code)
    print(json.dumps(out, indent=2)[:2500])
    print()

    print("✅ Testing complete!")
    print(f"Policy threshold configured: amount < {refund_limit}")


if __name__ == "__main__":
    main()
