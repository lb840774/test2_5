import io
import json
import time
import uuid
import zipfile
import boto3
from botocore.exceptions import ClientError

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

    lambda_exec_role_arn = cfg["lambda_exec_role_arn"]
    gateway_service_role_arn = cfg["gateway_service_role_arn"]

    gateway_name = f"{cfg.get('gateway_name_prefix','policy-gateway')}-{int(time.time())}"
    target_name = cfg.get("target_name", "RefundTarget")
    tool_name = cfg.get("tool_name", "process_refund")
    lambda_fn_name = f"{cfg.get('lambda_function_name_prefix','RefundTool')}-{int(time.time())}"

    lam = boto3.client("lambda", region_name=region)
    ac  = boto3.client("bedrock-agentcore-control", region_name=region)

    print("Region:", region)
    print("Gateway name:", gateway_name)
    print("Target/tool:", f"{target_name}___{tool_name}")
    print("Refund limit:", refund_limit)
    print("Using roles:")
    print(" - Lambda exec role:", lambda_exec_role_arn)
    print(" - Gateway service role:", gateway_service_role_arn)

    # 1) Create permissive Lambda tool
    lambda_code = f"""
def lambda_handler(event, context):
    amount = None
    try:
        if isinstance(event, dict):
            amount = event.get("amount") or (event.get("arguments") or {{}}).get("amount")
    except Exception:
        pass
    return {{
        "approved": True,
        "amount": amount,
        "note": "Lambda approves everything; Gateway policy should enforce limit.",
        "echo": event
    }}
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("lambda_function.py", lambda_code)
    zip_bytes = buf.getvalue()

    fn = lam.create_function(
        FunctionName=lambda_fn_name,
        Runtime="python3.11",
        Role=lambda_exec_role_arn,
        Handler="lambda_function.lambda_handler",
        Code={"ZipFile": zip_bytes},
        Timeout=15,
        MemorySize=128,
        Publish=True,
    )
    lambda_arn = fn["FunctionArn"]
    print("\n[1/6] Created Lambda:", lambda_arn)

    # Allow AgentCore service principal to invoke Lambda (resource policy)
    try:
        lam.add_permission(
            FunctionName=lambda_fn_name,
            StatementId=f"AllowAgentCoreInvoke-{uuid.uuid4().hex[:8]}",
            Action="lambda:InvokeFunction",
            Principal="bedrock-agentcore.amazonaws.com",
        )
        print("[1/6] Added Lambda permission for bedrock-agentcore.amazonaws.com")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            print("[1/6] Lambda permission already existed")
        else:
            raise

    # 2) Create Gateway (MCP) - NO authorizer for now
    gw = ac.create_gateway(
        name=gateway_name,
        description="Policy E2E gateway (boto3-only).",
        roleArn=gateway_service_role_arn,
        protocolType="MCP",
        authorizerType="NONE",
    )
    gateway_id  = gw["gatewayId"]
    gateway_arn = gw["gatewayArn"]
    gateway_url = gw["gatewayUrl"]
    print("\n[2/6] Created Gateway:", gateway_url)

    def gw_ready():
        g = ac.get_gateway(gatewayIdentifier=gateway_id)
        return (g["status"] == "READY"), g

    g = wait_for(gw_ready, desc="gateway READY")
    print("[2/6] Gateway status:", g["status"])

    # 3) Create Gateway Target with tool schema
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
        description="Refund tool target",
        targetConfiguration={
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": tool_schema}
                }
            }
        }
    )
    print("\n[3/6] Created Target:", {"name": target_name, "targetId": t.get("targetId")})

    # 4) Create Policy Engine
    pe = ac.create_policy_engine(
        name=f"refund-policy-engine-{int(time.time())}",
        description="Policy engine for refund governance"
    )
    policy_engine_id = pe["policyEngineId"]
    policy_engine_arn = pe["policyEngineArn"]
    print("\n[4/6] Created Policy Engine:", policy_engine_id)

    def pe_ready():
        p = ac.get_policy_engine(policyEngineId=policy_engine_id)
        return (p.get("status") in ("READY", "ACTIVE")), p

    p = wait_for(pe_ready, desc="policy engine READY/ACTIVE")
    print("[4/6] Policy engine status:", p.get("status"))

    # 5) Create Cedar policy
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
    print("\n[5/6] Created Policy:", policy_id)

    def pol_ready():
        pr = ac.get_policy(policyId=policy_id)
        return (pr.get("status") in ("READY", "ACTIVE")), pr

    pr = wait_for(pol_ready, desc="policy READY/ACTIVE")
    print("[5/6] Policy status:", pr.get("status"))

    # 6) Attach Policy Engine to Gateway in ENFORCE
    ac.update_gateway(
        gatewayIdentifier=gateway_id,
        policyEngineConfiguration={"arn": policy_engine_arn, "mode": "ENFORCE"},
    )
    print("\n[6/6] Attached Policy Engine to Gateway in ENFORCE mode")

    g2 = wait_for(gw_ready, desc="gateway READY after policy attach")
    print("[6/6] Gateway status:", g2["status"])

    runtime_cfg = {
        "region": region,
        "refund_limit": refund_limit,
        "gateway_id": gateway_id,
        "gateway_arn": gateway_arn,
        "gateway_url": gateway_url,
        "target_name": target_name,
        "tool_name": tool_name,
        "lambda_function_name": lambda_fn_name,
        "lambda_arn": lambda_arn,
        "policy_engine_id": policy_engine_id,
        "policy_engine_arn": policy_engine_arn,
        "policy_id": policy_id
    }

    with open("config_runtime.json", "w") as f:
        json.dump(runtime_cfg, f, indent=2)

    print("\n✅ Setup complete. Wrote config_runtime.json")

if __name__ == "__main__":
    main()
