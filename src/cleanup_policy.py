import json
import boto3

def main():
    with open("config_runtime.json", "r") as f:
        cfg = json.load(f)

    region = cfg["region"]
    ac = boto3.client("bedrock-agentcore-control", region_name=region)

    print("Cleanup starting... (best effort)")

    try:
        ac.delete_policy(policyId=cfg["policy_id"])
        print(" - deleted policy")
    except Exception as e:
        print(" - delete policy skipped:", str(e)[:200])

    try:
        ac.delete_policy_engine(policyEngineId=cfg["policy_engine_id"])
        print(" - deleted policy engine")
    except Exception as e:
        print(" - delete policy engine skipped:", str(e)[:200])

    try:
        targets = ac.list_gateway_targets(gatewayIdentifier=cfg["gateway_id"])
        for t in targets.get("targets", []):
            ac.delete_gateway_target(gatewayIdentifier=cfg["gateway_id"], targetId=t["targetId"])
        print(" - deleted gateway targets")
    except Exception as e:
        print(" - delete targets skipped:", str(e)[:200])

    try:
        ac.delete_gateway(gatewayIdentifier=cfg["gateway_id"])
        print(" - deleted gateway")
    except Exception as e:
        print(" - delete gateway skipped:", str(e)[:200])

    print("\n✅ Cleanup done. Existing Lambda was not touched.")

if __name__ == "__main__":
    main()
