"""
Cleanup script to remove Gateway and Policy Engine resources.

Run:
  python src/cleanup_policy.py

Reads:
  config_runtime.json
"""

import json
from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient
from bedrock_agentcore_starter_toolkit.operations.policy.client import PolicyClient


def main():
    with open("config_runtime.json", "r") as f:
        cfg = json.load(f)

    print("Cleaning up Policy Engine...")
    policy_client = PolicyClient(region_name=cfg["region"])
    policy_client.cleanup_policy_engine(cfg["policy_engine_id"])
    print("✓ Policy Engine cleaned up\n")

    print("Cleaning up Gateway + Cognito authorizer...")
    gateway_client = GatewayClient(region_name=cfg["region"])
    gateway_client.cleanup_gateway(cfg["gateway_id"], cfg["client_info"])
    print("✅ Cleanup complete!")


if __name__ == "__main__":
    main()
