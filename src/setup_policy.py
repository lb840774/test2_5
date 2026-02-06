import json
import time
from pathlib import Path

import boto3


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CFG_PATH = ROOT / "config.json"


def load_cfg():
    if not CFG_PATH.exists():
        raise SystemExit(f"Missing config.json at {CFG_PATH}")
    return json.loads(CFG_PATH.read_text())


def now_suffix():
    return str(int(time.time()))


def require(cfg, key):
    v = cfg.get(key)
    if not v:
        raise SystemExit(f"config.json missing required field: {key}")
    return v


def build_refund_tool_schema(refund_limit: int, tool_name: str):
    # Tool schema for MCP gateway -> Lambda tool
    # Keep it minimal and explicit
    return [
        {
            "name": tool_name,
            "description": f"Process a refund request up to ${refund_limit}.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "amount": {
                            "type": "number",
                            "description": f"Refund amount in USD (must be <= {refund_limit}).",
                        },
                        "reason": {"type": "string", "description": "Reason for refund."},
                    },
                    "required": ["amount", "reason"],
                }
            },
        }
    ]


def build_cedar_policy(refund_limit: int):
    """
    Cedar policy example: allow InvokeTool only if the requested refund amount <= refund_limit.
    This is a *real* policy string that the policy engine consumes.

    NOTE: Exact action/resource/entity names vary by service. The point of this E2E test is:
    - You attach a policy engine in ENFORCED mode
    - You run requests with amount <= limit (allowed) and > limit (denied)
    If your environment requires different principal/action names, we can adjust after you
    get the policy engine created successfully.
    """
    return f"""
permit(
  principal,
  action,
  resource
)
when {{
  // Allow only if the request context includes amount <= {refund_limit}
  context.amount <= {refund_limit}
}};
""".strip()


def main():
    cfg = load_cfg()

    region = cfg.get("region", "us-east-1")

    gateway_role_arn = require(cfg, "gateway_service_role_arn")
    gateway_prefix = cfg.get("gateway_name_prefix", "policy-gateway")
    target_name = cfg.get("target_name", "RefundTarget")
    tool_name = cfg.get("tool_name", "process_refund")

    refund_limit = int(cfg.get("refund_limit", 1000))

    existing_lambda_arn = require(cfg, "existing_lambda_arn")

    # Optional: if you already have one created
    policy_engine_arn = cfg.get("policy_engine_arn")
    policy_engine_mode = cfg.get("policy_engine_mode", "ENFORCED")

    print("Region:", region)
    print("Gateway prefix:", gateway_prefix)
    print("Target/tool:", target_name, "/", tool_name)
    print("Refund limit:", refund_limit)
    print("Gateway role:", gateway_role_arn)
    print("Using existing Lambda:", existing_lambda_arn)

    ac = boto3.client("bedrock-agentcore-control", region_name=region)

    # ---------------------------
    # 1) Ensure policy engine ARN
    # ---------------------------
    if not policy_engine_arn:
        cedar_policy = build_cedar_policy(refund_limit)

        # Try to create policy engine if API exists in this SDK
        if hasattr(ac, "create_policy_engine"):
            pe_name = f"{gateway_prefix}-pe-{now_suffix()}"
            print("Creating policy engine:", pe_name)

            # IMPORTANT: signature may differ slightly by region/SDK.
            # We'll try the most common pattern and fail loudly if it doesn't match.
            try:
                pe_resp = ac.create_policy_engine(
                    name=pe_name,
                    description="Refund policy engine (Cedar)",
                    roleArn=gateway_role_arn,
                    policyType="CEDAR",
                    policyContent=cedar_policy,
                )
            except TypeError as e:
                raise SystemExit(
                    "Your boto3 model for create_policy_engine has a different signature.\n"
                    "Run this in a notebook cell to see required params:\n\n"
                    "import boto3, json\n"
                    "ac=boto3.client('bedrock-agentcore-control', region_name='us-east-1')\n"
                    "op=ac.meta.service_model.operation_model('CreatePolicyEngine')\n"
                    "print(op.input_shape.members.keys())\n\n"
                    f"Original error: {e}"
                )

            # Try a few likely response shapes
            policy_engine_arn = (
                pe_resp.get("arn")
                or (pe_resp.get("policyEngine") or {}).get("arn")
                or (pe_resp.get("policy_engine") or {}).get("arn")
            )
            if not policy_engine_arn:
                raise SystemExit(
                    "Created policy engine but could not find arn in response.\n"
                    f"Response keys: {list(pe_resp.keys())}\n"
                    "Paste the response and I’ll map the field."
                )

            print("Policy engine ARN:", policy_engine_arn)
            print(
                "\n✅ Add this to config.json so you don't need to re-create next time:\n"
                f'  "policy_engine_arn": "{policy_engine_arn}"\n'
            )
        else:
            # Can't create in this environment via SDK, so require it in config
            raise SystemExit(
                "Your boto3 client does NOT expose create_policy_engine(), but CreateGateway requires "
                "policyEngineConfiguration.\n\n"
                "Fix: create a Policy Engine once (console/other pipeline) and add to config.json:\n"
                '  "policy_engine_arn": "arn:..."\n'
            )
    else:
        print("Using policy engine ARN from config.json:", policy_engine_arn)

    # ---------------------------
    # 2) Create Gateway (MCP)
    # ---------------------------
    gateway_name = f"{gateway_prefix}-{now_suffix()}"
    print("\nCreating gateway:", gateway_name)

    # Your schema says CreateGateway supports: protocolConfiguration (mcp...) and policyEngineConfiguration (arn, mode)
    gw_resp = ac.create_gateway(
        name=gateway_name,
        description="Policy E2E gateway (Lambda target)",
        roleArn=gateway_role_arn,
        protocolType="MCP",
        protocolConfiguration={
            "mcp": {
                "supportedVersions": ["2024-11-05"],
                "instructions": (
                    "You are a policy-protected gateway. "
                    "Call the refund tool only when allowed by policy."
                ),
                "searchType": "NONE",
            }
        },
        authorizerType="NONE",
        policyEngineConfiguration={
            "arn": policy_engine_arn,
            "mode": policy_engine_mode,
        },
        tags={"purpose": "agentcore-policy-e2e"},
    )

    gateway_id = (
        gw_resp.get("gatewayId")
        or (gw_resp.get("gateway") or {}).get("gatewayId")
        or (gw_resp.get("gateway") or {}).get("id")
        or gw_resp.get("id")
    )
    gateway_endpoint = (
        gw_resp.get("endpoint")
        or (gw_resp.get("gateway") or {}).get("endpoint")
        or (gw_resp.get("gateway") or {}).get("mcpEndpoint")
    )

    if not gateway_id:
        raise SystemExit(
            "Could not find gateway id in create_gateway response.\n"
            f"Response keys: {list(gw_resp.keys())}\n"
            "Paste gw_resp and I’ll map the field."
        )

    print("Gateway id:", gateway_id)
    if gateway_endpoint:
        print("Gateway endpoint:", gateway_endpoint)

    # ---------------------------
    # 3) Create Gateway Target (Lambda tool)
    # ---------------------------
    tool_schema = build_refund_tool_schema(refund_limit, tool_name)

    print("\nCreating gateway target:", target_name)
    tgt_resp = ac.create_gateway_target(
        gatewayId=gateway_id,
        name=target_name,
        targetConfiguration={
            "mcp": {
                "lambda": {
                    "lambdaArn": existing_lambda_arn,
                    "toolSchema": {"inlinePayload": tool_schema},
                }
            }
        },
    )

    target_id = (
        tgt_resp.get("targetId")
        or (tgt_resp.get("target") or {}).get("targetId")
        or tgt_resp.get("id")
    )
    print("Target created:", target_id or "(id not returned)")

    # ---------------------------
    # 4) Write outputs for later steps/tests
    # ---------------------------
    out = {
        "region": region,
        "gateway_name": gateway_name,
        "gateway_id": gateway_id,
        "gateway_endpoint": gateway_endpoint,
        "target_name": target_name,
        "tool_name": tool_name,
        "existing_lambda_arn": existing_lambda_arn,
        "policy_engine_arn": policy_engine_arn,
        "refund_limit": refund_limit,
    }

    (ROOT / "outputs.json").write_text(json.dumps(out, indent=2))
    print("\nWrote outputs.json. Next run: python src/test_policy.py")


if __name__ == "__main__":
    main()
