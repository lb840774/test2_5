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


def safe_get(d: Dict[str, Any], *keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def print_header(cfg: Dict[str, Any]) -> None:
    print(f"\n{PROJECT_ROOT}")
    print(f"Region: {cfg['region']}")
    print(f"Gateway prefix: {cfg.get('gateway_name_prefix', 'policy-gateway')}")
    print(f"Target/tool: {cfg.get('target_name','RefundTarget')} / {cfg.get('tool_name','process_refund')}")
    print(f"Refund limit: {cfg.get('refund_limit', 1000)}")
    print(f"Gateway role: {cfg.get('gateway_service_role_arn')}")
    print(f"Using existing Lambda: {cfg.get('existing_lambda_arn')}\n")


def tool_schema(tool_name: str) -> Dict[str, Any]:
    # A simple schema AgentCore can expose to models/tools:
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
    # NOTE: Cedar syntax can vary by example. The key point is:
    # - enforce based on an attribute (amount) and the tool action.
    # We keep it simple: allow the tool only when amount <= limit.
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


def create_gateway(ac, name: str, role_arn: str) -> Dict[str, Any]:
    # This matches the “create gateway” behavior you already saw succeed.
    resp = ac.create_gateway(
        name=name,
        description="MCP Gateway for AgentCore policy E2E test",
        roleArn=role_arn,
        clientToken=str(uuid.uuid4()),
        protocolType="MCP",
    )
    return resp


def wait_gateway_ready(ac, gateway_arn: str, timeout_s: int = 120) -> None:
    t0 = time.time()
    while True:
        r = ac.get_gateway(gatewayArn=gateway_arn)
        status = r.get("status")
        if status in ("READY", "ACTIVE"):
            return
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
    Your environment shows:
    - policyEngineConfiguration exists on CreateGatewayTarget
    - but CreatePolicyEngine doesn't accept policy content
    So we attach policy inline at target creation.

    Because SDK models vary, we try a few shapes.
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
        # IMPORTANT: do NOT include credentialProvider anywhere.
        # Your errors show that field is not allowed.
    )

    # Variant A: policyEngineConfiguration with INLINE arn + cedar bundle
    variant_a = dict(base_req)
    variant_a["policyEngineConfiguration"] = {
        "arn": "INLINE",
        "mode": "ENFORCE",
        "cedar": {
            "policies": [
                {"policyId": "refund-policy", "policyContent": cedar}
            ]
        },
    }

    # Variant B: policyEngineConfiguration without arn (some models require only mode + cedar)
    variant_b = dict(base_req)
    variant_b["policyEngineConfiguration"] = {
        "mode": "ENFORCE",
        "cedar": {
            "policies": [
                {"policyId": "refund-policy", "policyContent": cedar}
            ]
        },
    }

    # Variant C: policyEngineConfiguration only references arn/mode (if inline policy not supported)
    # (You would then need an existing policy engine ARN in config. This is a fallback.)
    variant_c = dict(base_req)
    pe_arn = None
    # allow either policy_engine_arn or policyEngineArn in config if you add it later
    # but we won’t require it now
    # If missing, we skip this variant.
    # (kept for future)
    # variant_c["policyEngineConfiguration"] = {"arn": pe_arn, "mode": "ENFORCE"}

    attempts = [
        ("A (INLINE arn + cedar.policies)", variant_a),
        ("B (no arn + cedar.policies)", variant_b),
    ]

    last_err: Optional[Exception] = None
    for label, req in attempts:
        try:
            print(f"[2/3] Creating Gateway Target using policy variant {label} ...")
            resp = ac.create_gateway_target(**req)
            return resp
        except (ParamValidationError, ClientError) as e:
            last_err = e
            msg = str(e)
            print(f"  -> Variant {label} failed.")
            # helpful prints (short)
            if "Unknown parameter" in msg or "Parameter validation failed" in msg:
                print("     (SDK model mismatch on policy fields; trying next variant)")
            else:
                print(f"     {msg[:400]}")
            continue

    # If we got here, none worked.
    raise SystemExit(
        "\nNone of the inline policy variants worked in your environment.\n"
        "This usually means your account/SDK requires an existing Policy Engine ARN.\n"
        "If you can obtain a pre-created policy engine ARN from your platform team, add it to config.json as:\n"
        '  "policy_engine_arn": "arn:aws:bedrock-agentcore:...:policy-engine/..." \n'
        "and I’ll give you the exact single-call Target config for that.\n"
        f"\nLast error: {last_err}\n"
    )


def main():
    cfg = load_config()

    # REQUIRED config keys
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
    gw_resp = create_gateway(ac, gateway_name, gateway_role)

    gateway_arn = gw_resp.get("gatewayArn") or gw_resp.get("arn")
    gateway_endpoint = gw_resp.get("gatewayEndpoint") or gw_resp.get("endpoint") or gw_resp.get("url")

    if not gateway_arn:
        # print full response for debugging
        print("CreateGateway response:\n", json.dumps(gw_resp, indent=2, default=str))
        raise SystemExit("Could not find gatewayArn in CreateGateway response.")

    # wait until READY
    wait_gateway_ready(ac, gateway_arn)
    # re-fetch to get endpoint if needed
    gw_get = ac.get_gateway(gatewayArn=gateway_arn)
    gateway_endpoint = gateway_endpoint or gw_get.get("gatewayEndpoint") or gw_get.get("endpoint") or gw_get.get("url")

    if gateway_endpoint:
        print(f"Gateway READY: {gateway_endpoint}")
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
    if gateway_endpoint:
        print(f"MCP URL     : {gateway_endpoint}/mcp" if not str(gateway_endpoint).endswith("/mcp") else f"MCP URL     : {gateway_endpoint}")
    print(f"Target ARN  : {target_arn}")

    print("\nNext step:")
    print(" - Run your runtime deploy / invoke scripts (deploy_runtime_jwt.py, invoke_runtime_jwt.py) as you planned.")
    print(" - For policy proof: invoke tool with amount <= limit (allowed) and > limit (denied).")


if __name__ == "__main__":
    main()
