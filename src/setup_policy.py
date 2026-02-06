"""
Setup script to create Gateway with Policy Engine (AWS tutorial style, but reuses existing IAM roles)

Run:
  python src/setup_policy.py

Reads:
  config.json

Writes:
  config_runtime.json  (contains created resource IDs + client_info)
"""

import json
import logging
import time
import boto3

from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient
from bedrock_agentcore_starter_toolkit.operations.policy.client import PolicyClient
from bedrock_agentcore_starter_toolkit.utils.lambda_utils import create_lambda_function


def main():
    with open("config.json", "r") as f:
        cfg = json.load(f)

    region = cfg["region"]
    refund_limit = int(cfg["refund_limit"])
    lambda_exec_role_arn = cfg["lambda_exec_role_arn"]
    gateway_service_role_arn = cfg["gateway_service_role_arn"]
    gateway_name_prefix = cfg.get("gateway_name_prefix", "policy-gateway")
    lambda_fn_prefix = cfg.get("lambda_function_name_prefix", "RefundTool")
    target_name = cfg.get("target_name", "RefundTarget")
    tool_name = cfg.get("tool_name", "process_refund")

    print("Setting up AgentCore Gateway with Policy Engine (replicating AWS tutorial)...")
    print(f"Region: {region}")
    print(f"Refund limit: {refund_limit}")
    print(f"Using existing Lambda exec role: {lambda_exec_role_arn}")
    print(f"Using existing Gateway service role: {gateway_service_role_arn}\n")

    # Initialize clients (starter toolkit)
    gateway_client = GatewayClient(region_name=region)
    gateway_client.logger.setLevel(logging.INFO)

    policy_client = PolicyClient(region_name=region)
    policy_client.logger.setLevel(logging.INFO)

    # Step 1: Create OAuth authorizer (Cognito)
    print("Step 1: Creating OAuth authorization server (Cognito)...")
    cognito_response = gateway_client.create_oauth_authorizer_with_cognito(gateway_name_prefix)
    print("✓ Authorization server created\n")

    # Step 2: Create Gateway (MCP)
    print("Step 2: Creating Gateway (MCP endpoint)...")
    # IMPORTANT: we pass role_arn explicitly to avoid any role creation behavior.
    gateway = gateway_client.create_mcp_gateway(
        name=None,
        role_arn=gateway_service_role_arn,
        authorizer_config=cognito_response["authorizer_config"],
        enable_semantic_search=False,
    )
    print(f"✓ Gateway created: {gateway['gatewayUrl']}\n")

    # Step 3: Create Lambda function with refund tool (intentionally permissive)
    print("Step 3: Creating Lambda function (tool implementation)...")
    refund_lambda_code = f"""
def lambda_handler(event, context):
    # Intentionally permissive: approves any amount.
    amount = event.get('amount', 0)
    return {{
        "status": "success",
        "message": f"Refund of ${{amount}} processed successfully (tool did not enforce limit)",
        "amount": amount
    }}
"""

    session = boto3.Session(region_name=region)

    # create_lambda_function lets us pass gateway role; we also pass our exec role
    # NOTE: lambda_utils implementation can vary by version; if it doesn't support exec_role_arn,
    # we fall back to default behavior of the toolkit.
    lambda_arn = create_lambda_function(
        session=session,
        logger=gateway_client.logger,
        function_name=f"{lambda_fn_prefix}-{int(time.time())}",
        lambda_code=refund_lambda_code,
        runtime="python3.11",
        handler="lambda_function.lambda_handler",
        gateway_role_arn=gateway["roleArn"],
        description="Refund tool for policy demo",
        exec_role_arn=lambda_exec_role_arn,  # if unsupported in your toolkit version, remove this line
    )
    print(f"✓ Lambda function created: {lambda_arn}\n")

    # Step 4: Add Lambda target with refund tool schema
    print("Step 4: Adding Lambda target + tool schema...")
    tool_schema = {
        "inlinePayload": [
            {
                "name": tool_name,
                "description": "Process a customer refund",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "amount": {
                            "type": "integer",
                            "description": "Refund amount in dollars"
                        }
                    },
                    "required": ["amount"]
                }
            }
        ]
    }

    lambda_target = gateway_client.create_mcp_gateway_target(
        gateway=gateway,
        name=target_name,
        target_type="lambda",
        target_payload={
            "lambdaArn": lambda_arn,
            "toolSchema": tool_schema
        },
        credentials=None
    )
    print("✓ Lambda target added\n")

    # Step 5: Create Policy Engine
    print("Step 5: Creating Policy Engine...")
    engine = policy_client.create_or_get_policy_engine(
        name="RefundPolicyEngine",
        description="Policy engine for refund governance"
    )
    print(f"✓ Policy Engine created: {engine['policyEngineId']}\n")

    # Step 6: Create Cedar policy (doc pattern)
    print(f"Step 6: Creating Cedar policy (allow < {refund_limit})...")
    cedar_statement = (
        f'permit(principal, '
        f'action == AgentCore::Action::"{target_name}___{tool_name}", '
        f'resource == AgentCore::Gateway::"{gateway["gatewayArn"]}") '
        f'when {{ context.input.amount < {refund_limit} }};'
    )

    policy = policy_client.create_or_get_policy(
        policy_engine_id=engine["policyEngineId"],
        name="refund_limit_policy",
        description=f"Allow refunds under ${refund_limit}",
        definition={"cedar": {"statement": cedar_statement}},
    )
    print(f"✓ Policy created: {policy['policyId']}\n")

    # Step 7: Attach Policy Engine to Gateway (ENFORCE)
    print("Step 7: Attaching Policy Engine to Gateway (ENFORCE mode)...")
    gateway_client.update_gateway_policy_engine(
        gateway_identifier=gateway["gatewayId"],
        policy_engine_arn=engine["policyEngineArn"],
        mode="ENFORCE"
    )
    print("✓ Policy Engine attached to Gateway\n")

    # Step 8: Save runtime configuration for test/cleanup
    runtime = {
        "region": region,
        "refund_limit": refund_limit,
        "gateway_url": gateway["gatewayUrl"],
        "gateway_id": gateway["gatewayId"],
        "gateway_arn": gateway["gatewayArn"],
        "gateway_role_arn": gateway["roleArn"],
        "policy_engine_id": engine["policyEngineId"],
        "policy_engine_arn": engine["policyEngineArn"],
        "policy_id": policy["policyId"],
        "client_info": cognito_response["client_info"],
        "target_name": target_name,
        "tool_name": tool_name,
        "lambda_arn": lambda_arn,
    }

    with open("config_runtime.json", "w") as f:
        json.dump(runtime, f, indent=2)

    print("✅ Setup complete. Wrote config_runtime.json")


if __name__ == "__main__":
    main()
