#!/usr/bin/env python3
import json
import os
import time
import uuid
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError, ParamValidationError

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")


def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        raise SystemExit(f"config.json not found at: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def now_suffix() -> str:
    return str(int(time.time()))


def print_header(cfg: Dict[str, Any]) -> None:
    print(f"\n{PROJECT_ROOT}")
    print(f"Region: {cfg['region']}")
    print(f"Gateway prefix: {cfg.get('gateway_name_prefix', 'policy-gateway')}")
    print(f"Target/tool: {cfg.get('target_name','RefundTarget')} / {cfg.get('tool_name','process_refund')}")
    print(f"Refund limit: {cfg.get('refund_limit', 1000)}")
    print(f"Gateway role: {cfg.get('gateway_service_role_arn')}")
    print(f"Using existing Lambda: {cfg.get('existing_lambda_arn')}")
    print(f"Authorizer type: {cfg.get('authorizer_type','NONE')}\n")


def tool_schema(tool_name: str) -> Dict[str, Any]:
    return {
        "name": tool_name,
        "description": "Process a refund request (demo tool).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "description": "Refund amount in USD."},
                "reason": {"type": "string", "description": "Reason for the refund."},
                "orderId": {"type": "string", "description": "Order identifier."},
            },
            "required": ["amount", "reason"],
            "additionalProperties": False,
        },
    }


def cedar_policy_text(refund_limit: int, tool_name: str) -> str:
    return f"""
permit (
  principal,
  action == Action::"{tool_name}",
  resource
)
when {{
  resource.amount <= {refund_limit}
}};
""".strip()


def create_gateway(ac, name: str, role_arn: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Your account requires authorizerType for MCP gateways.
    Start with authorizer_type=NONE in config.json.
    If your account requires JWT, set authorizer_type=JWT and add jwt_issuer/jwt_audience.
    """
    auth_type = (cfg.get("authorizer_type") or "NONE").upper()

    req = dict(
        name=name,
        description="MCP Gateway for AgentCore policy E2E test",
        roleArn=role_arn,
        clientToken=str(uuid.uuid4()),
        protocolType="MCP",
        authorizerType=auth_type,  # ✅ REQUIRED (fixes your current error)
    )

    if auth_type == "JWT":
        issuer = cfg["jwt_issuer"]
        audience = cfg["jwt_audience"]
        req["authorizerConfiguration"] = {
            "jwt": {
                "issuer": issuer,
                "audience": [audience] if isinstance(audience, str) else audience,
            }
        }

    return ac.create_gateway(**req)


def wait_gateway_ready(ac, gateway_arn: str, timeout_s: int = 180) -> Dict[str, Any]:
    t0 = time.time()
    last = None
    while True:
        last = ac.get_gateway(gatewayArn=gateway_arn)
        status = last.get("status")
        if status in ("READY", "ACTIVE"):
            return last
        if time.time() - t0 > timeout_s:
            raise SystemExit(f"Gateway did not become READY in {timeout_s}s. Last status={status}")
        time.sleep(3)


def try_create_gateway_target(
    ac,
    gateway_arn: str,
    target_name: str,
    lambda_arn: str,
    tool_name: str,
    refund_limit: int,
) -> Dict[str, Any]:
    """
    We attempt two inline-policy shapes because accounts differ.
    We DO NOT send credentialProvider anywhere.
    """
    ts = tool_schema(tool_name)
    cedar = cedar_policy_text(refund_limit, tool_name)

    base_req = dict(
        gatewayArn=gateway_arn,
        name=target_name,
        description="Refund tool target (Lambda) behind MCP gateway",
        clientToken=str(uuid.uuid4()),
        protocolType="MCP",
        protocolConfiguration={
            "mcp": {
                "supportedVersions": ["2024-11-05"],
                "instructions": "Refund tool MCP target",
            }
        },
        targetConfiguration={
            "lambda": {
                "lambdaArn": lambda_arn,
                "toolSchema": ts,
            }
        },
    )

    # Variant A
    variant_a = dict(base_req)
    variant_a["policyEngineConfiguration"] = {
        "arn": "INLINE",
        "mode": "ENFORCE",
        "cedar": {"policies": [{"policyId": "refund-policy", "policyContent": cedar}]},
    }

    # Variant B
    variant_b = dict(base_req)
    variant_b["policyEngineConfiguration"] = {
        "mode": "ENFORCE",
        "cedar": {"policies": [{"policyId": "refund-policy", "policyContent": cedar}]},
    }

    attempts = [
        ("A (INLINE arn + cedar.policies)", variant_a),
        ("B (no arn + cedar.policies)", variant_b),
    ]

    last_err: Optional[Exception] = None
    for label, req in attempts:
        try:
            print(f"[2/3] Creating Gateway Target using policy variant {label} ...")
            return ac.create_gateway_target(**req)
        except (ParamValidationError, ClientError) as e:
            last_err = e
            print(f"  -> Variant {label} failed: {str(e)[:350]}")
            continue

    raise SystemExit(
        "\nGateway created, but Target creation failed for both inline policy shapes.\n"
        "This usually means your account requires a pre-created Policy Engine ARN (docs path).\n"
        f"\nLast error: {last_err}\n"
    )


def main():
    cfg = load_config()
    region = cfg["region"]
    gateway_role = cfg["gateway_service_role_arn"]
    existing_lambda_arn = cfg["existing_lambda_arn"]

    gateway_prefix = cfg.get("gateway_name_prefix", "policy-gateway")
    gateway_name = f"{gateway_prefix}-{now_suffix()}"

    target_name = cfg.get("target_name", "RefundTarget")
    tool_name = cfg.get("tool_name", "process_refund")
    refund_limit = int(cfg.get("refund_limit", 1000))

    print_header(cfg)

    ac = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"[1/3] Creating Gateway: {gateway_name}")
    gw_resp = create_gateway(ac, gateway_name, gateway_role, cfg)

    gateway_arn = gw_resp.get("gatewayArn") or gw_resp.get("arn")
    if not gateway_arn:
        print("CreateGateway response:\n", json.dumps(gw_resp, indent=2, default=str))
        raise SystemExit("Could not find gatewayArn in CreateGateway response.")

    gw_get = wait_gateway_ready(ac, gateway_arn)
    endpoint = (
        gw_resp.get("gatewayEndpoint")
        or gw_get.get("gatewayEndpoint")
        or gw_resp.get("endpoint")
        or gw_get.get("endpoint")
        or gw_resp.get("url")
        or gw_get.get("url")
    )

    print(f"Gateway status: {gw_get.get('status')}")
    if endpoint:
        if str(endpoint).endswith("/mcp"):
            print(f"MCP URL: {endpoint}")
        else:
            print(f"MCP URL: {endpoint}/mcp")
    else:
        print("Gateway READY (endpoint not returned by this SDK response).")

    target_resp = try_create_gateway_target(
        ac=ac,
        gateway_arn=gateway_arn,
        target_name=target_name,
        lambda_arn=existing_lambda_arn,
        tool_name=tool_name,
        refund_limit=refund_limit,
    )

    target_arn = target_resp.get("targetArn") or target_resp.get("arn")
    print("\n[3/3] DONE")
    print(f"Gateway ARN : {gateway_arn}")
    print(f"Target ARN  : {target_arn}")

    print("\nNext step:")
    print("Run your runtime deploy/invoke scripts and test amounts <= limit (allow) and > limit (deny).")


if __name__ == "__main__":
    main()
