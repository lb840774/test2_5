import os
import json
import uuid
import requests

def _mcp_call(gateway_url: str, token: str, tool_full_name: str, amount: int):
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {
            "name": tool_full_name,
            "arguments": {"amount": int(amount)}
        }
    }
    r = requests.post(
        gateway_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json=payload,
        timeout=20,
    )
    return r.status_code, r.json()

def handler(event, context=None):
    """
    Expected input payload (JSON):
      {
        "amount": 500,
        "gateway_url": "...",
        "access_token": "...",
        "tool_full_name": "RefundTarget___process_refund"
      }
    """
    amount = int(event["amount"])
    gateway_url = event["gateway_url"]
    access_token = event["access_token"]
    tool_full_name = event["tool_full_name"]

    status, resp = _mcp_call(gateway_url, access_token, tool_full_name, amount)

    return {
        "runtime_observed": {
            "amount": amount,
            "tool": tool_full_name,
            "gateway_http_status": status
        },
        "gateway_response": resp
    }
