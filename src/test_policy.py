import json
import uuid
import requests

def mcp(gateway_url: str, method: str, params=None):
    payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method}
    if params is not None:
        payload["params"] = params
    r = requests.post(gateway_url, headers={"Content-Type":"application/json"}, json=payload, timeout=30)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"_non_json": r.text}

def main():
    with open("config_runtime.json", "r") as f:
        cfg = json.load(f)

    gateway_url = cfg["gateway_url"]
    target_name = cfg["target_name"]
    tool_name = cfg["tool_name"]
    refund_limit = int(cfg["refund_limit"])

    tool_full_name = f"{target_name}___{tool_name}"

    print("Gateway URL:", gateway_url)

    print("\n=== tools/list ===")
    code, out = mcp(gateway_url, "tools/list")
    print("HTTP", code)
    print(json.dumps(out, indent=2)[:2500])

    print(f"\n=== tools/call ALLOW: amount={refund_limit-1} ===")
    code, out = mcp(gateway_url, "tools/call", {"name": tool_full_name, "arguments": {"amount": refund_limit-1}})
    print("HTTP", code)
    print(json.dumps(out, indent=2)[:2500])

    print(f"\n=== tools/call DENY: amount={refund_limit+1} ===")
    code, out = mcp(gateway_url, "tools/call", {"name": tool_full_name, "arguments": {"amount": refund_limit+1}})
    print("HTTP", code)
    print(json.dumps(out, indent=2)[:2500])

    print("\n✅ If ALLOW succeeds and DENY fails, Gateway policy enforcement is confirmed.")

if __name__ == "__main__":
    main()
