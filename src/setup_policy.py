import json
import time
import boto3

def wait_for(fn, timeout_s=420, sleep_s=5, desc="wait"):
    start = time.time()
    while True:
        ok, val = fn()
        if ok:
            return val
        if time.time() - start > timeout_s:
            raise TimeoutError(f"Timed out: {desc}")
        time.sleep(sleep_s)

def main():
    with open("config.json", "r") as f:
        cfg = json.load(f)

    region = cfg["region"]
    refund_limit = int(cfg["refund_limit"])

    gateway_service_role_arn = cfg["gateway_service_role_arn"]
    gateway_name = f"{cfg.get('gateway_name_prefix','policy-gateway')}-{int(time.time())}"
    target_name = cfg.get("target_name", "RefundTarget")
    tool_name = cfg.get("tool_name", "process_refund")

    if not cfg.get("existing_lambda_arn"):
        raise RuntimeError("existing_lambda_arn is required (we are not creating lambdas from notebook).")

    lambda_arn = cfg["existing_lambda_arn"]
    lambda_name = lambda_arn.split(":function:")[-1]

    ac = boto3.client("bedrock-agentcore-control", region_name=region)

    print("Region:", region)
    print("Gateway name:", gateway_name)
    print("Target/tool:", f"{target_name}___{tool_name}")
    print("Refund limit:", refund_limit)
    print("Gateway service role:", gateway_service_role_arn)
    print("Using existing Lambda:", lambda_arn)

    # 1) Create Gateway (MCP) - authorizer NONE (no JWT needed)
    gw = ac.create_gateway(
        name=gateway_name,
        description="Policy E2E gateway (existing lambda).",
        roleArn=gateway_service_role_arn,
        protocolType="MCP",
        authorizerType="NONE",
    )
    gateway_id = gw["gatewayId"]
    gateway_arn = gw["gatewayArn"]
    gateway_url = gw["gatewayUrl"]
    print("\n[1/5] Created Gateway:", gateway_url)

    def gw_ready():
        g = ac.get_gateway(gatewayIdentifier=gateway_id)
        return (g["status"] == "READY"), g

    g = wait_for(gw_ready, desc="gateway READY")
    print("[1/5] Gateway status:", g["status"])

    # 2) Create Gateway Target with tool schema
    tool_schema = [{
        "name": tool_name,
        "description": "Process a refund",
        "inputSchema": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "description": "Refund amount"}
            },
            "required": ["amount"]
        }
    }]

    t = ac.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=target_name,
        description="Refund tool target backed by existing Lambda",
        targetConfiguration={
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": tool_schema}
                }
            }
        }
    )
    target_id = t.get("targetId")
    print("\n[2/5] Created Target:", {"name": target_name, "targetId": target_id})

    # 3) Create Policy Engine
    pe = ac.create_policy_engine(
        name=f"refund-policy-engine-{int(time.time())}",
        description="Policy engine for refund governance"
    )
    policy_engine_id = pe["policyEngineId"]
    policy_engine_arn = pe["policyEngineArn"]
    print("\n[3/5] Created Policy Engine:", policy_engine_id)

    def pe_ready():
        p = ac.get_policy_engine(policyEngineId=policy_engine_id)
        return (p.get("status") in ("READY", "ACTIVE")), p

    p = wait_for(pe_ready, desc="policy engine READY/ACTIVE")
    print("[3/5] Policy engine status:", p.get("status"))

    # 4) Create Cedar policy: allow only amount < refund_limit
    action_name = f"{target_name}___{tool_name}"
    cedar = f'''permit(principal,
  action == AgentCore::Action::"{action_name}",
  resource == AgentCore::Gateway::"{gateway_arn}")
when {{
  context.input.amount < {refund_limit}
}};'''

    pol = ac.create_policy(
        name=f"refund-under-{refund_limit}-{int(time.time())}",
        description=f"Allow refunds with amount < {refund_limit}",
        definition={"cedar": {"statement": cedar}},
        validationMode="FAIL_ON_ANY_FINDINGS",
        policyEngineId=policy_engine_id,
    )
    policy_id = pol["policyId"]
    print("\n[4/5] Created Policy:", policy_id)

    def pol_ready():
        pr = ac.get_policy(policyId=policy_id)
        return (pr.get("status") in ("READY", "ACTIVE")), pr

    pr = wait_for(pol_ready, desc="policy READY/ACTIVE")
    print("[4/5] Policy status:", pr.get("status"))

    # 5) Attach policy engine to gateway (ENFORCE)
    ac.update_gateway(
        gatewayIdentifier=gateway_id,
        policyEngineConfiguration={"arn": policy_engine_arn, "mode": "ENFORCE"},
    )
    print("\n[5/5] Attached Policy Engine to Gateway in ENFORCE mode")

    g2 = wait_for(gw_ready, desc="gateway READY after policy attach")
    print("[5/5] Gateway status:", g2["status"])

    runtime_cfg = {
        "region": region,
        "refund_limit": refund_limit,
        "gateway_id": gateway_id,
        "gateway_arn": gateway_arn,
        "gateway_url": gateway_url,
        "target_name": target_name,
        "tool_name": tool_name,
        "gateway_target_id": target_id,
        "policy_engine_id": policy_engine_id,
        "policy_engine_arn": policy_engine_arn,
        "policy_id": policy_id,
        "existing_lambda_arn": lambda_arn,
        "existing_lambda_name": lambda_name
    }

    with open("config_runtime.json", "w") as f:
        json.dump(runtime_cfg, f, indent=2)

    print("\n✅ Setup complete. Wrote config_runtime.json")

if __name__ == "__main__":
    main()
