"""Custom resource: provisions AgentCore Gateway inference routing.

CloudFormation does not yet support Gateway inference targets or interceptor
configurations, so this Lambda owns their lifecycle (create/update/delete).
"""
import json
import time
import urllib.request

import boto3

acc = boto3.client("bedrock-agentcore-control")


def send(event, context, status, data=None, reason=""):
    body = json.dumps({
        "Status": status, "Reason": reason or f"see {context.log_stream_name}",
        "PhysicalResourceId": data.get("GatewayId", event.get("PhysicalResourceId", "none")) if data else event.get("PhysicalResourceId", "none"),
        "StackId": event["StackId"], "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"], "Data": data or {},
    }).encode()
    req = urllib.request.Request(event["ResponseURL"], data=body, method="PUT",
                                 headers={"Content-Type": ""})
    urllib.request.urlopen(req)


def wait_ready(getter, key, ident, states=("READY",), timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = getter(**{key: ident})["status"]
        if st in states:
            return
        if st.endswith("FAILED"):
            raise RuntimeError(f"{ident} entered {st}")
        time.sleep(5)
    raise TimeoutError(f"{ident} not ready in {timeout}s")


def create_all(p):
    # fresh IAM roles can take ~10-30s to propagate; retry AccessDenied
    for attempt in range(6):
        try:
            gw = _create_gateway(p)
            break
        except Exception as e:  # noqa: BLE001
            if "AccessDenied" in str(e) and attempt < 5:
                time.sleep(10)
                continue
            raise
    return gw


def _find_by_name(name):
    for g in acc.list_gateways().get("items", []):
        if g["name"] == name:
            return acc.get_gateway(gatewayIdentifier=g["gatewayId"])
    return None


def _create_gateway(p):
    existing = _find_by_name(p["GatewayName"])
    if existing:  # idempotent re-create (CFN retry or prior partial failure)
        gw = existing
        gid = gw["gatewayId"]
    else:
        gw = _do_create(p)
        gid = gw["gatewayId"]
    try:
        return _finish(p, gw, gid)
    except Exception:
        delete_all(gid)  # don't orphan a half-built gateway
        raise


def _do_create(p):
    gw = acc.create_gateway(
        name=p["GatewayName"],
        roleArn=p["GatewayRoleArn"],
        protocolType="MCP",
        authorizerType="AWS_IAM",
        interceptorConfigurations=[{
            "interceptor": {"lambda": {"arn": p["RouterLambdaArn"]}},
            "interceptionPoints": ["REQUEST"],
            "inputConfiguration": {"passRequestHeaders": False},
        }],
    )
    wait_ready(acc.get_gateway, "gatewayIdentifier", gw["gatewayId"])
    return gw


def _finish(p, gw, gid):
    region = boto3.session.Session().region_name
    # idempotent target: reuse if present
    for t in acc.list_gateway_targets(gatewayIdentifier=gid).get("items", []):
        if t["name"] == "runtime":
            url = acc.get_gateway(gatewayIdentifier=gid)["gatewayUrl"].replace("/mcp", "")
            return {"GatewayId": gid, "GatewayArn": gw["gatewayArn"],
                    "InferenceUrl": url + "/inference",
                    "ChatCompletionsUrl": url + "/inference/v1/chat/completions",
                    "MessagesUrl": url + "/inference/v1/messages", "TargetId": t["targetId"]}
    tgt = acc.create_gateway_target(
        gatewayIdentifier=gid,
        name="runtime",
        targetConfiguration={"inference": {"provider": {
            "endpoint": f"https://bedrock-runtime.{region}.amazonaws.com",
            "operations": [
                {"path": "/v1/chat/completions",
                 "providerPath": "/openai/v1/chat/completions",
                 "models": [{"model": m} for m in json.loads(p["OpenAiFamilyModels"])]},
                {"path": "/v1/messages",
                 "providerPath": "/anthropic/v1/messages",
                 "models": [{"model": m} for m in json.loads(p["AnthropicFamilyModels"])]},
            ],
        }}},
        credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    )
    t0 = time.time()
    while time.time() - t0 < 300:
        st = acc.get_gateway_target(gatewayIdentifier=gid, targetId=tgt["targetId"])["status"]
        if st == "READY":
            break
        if st.endswith("FAILED"):
            raise RuntimeError(f"target {st}")
        time.sleep(5)
    url = acc.get_gateway(gatewayIdentifier=gid)["gatewayUrl"].replace("/mcp", "")
    return {"GatewayId": gid, "GatewayArn": gw["gatewayArn"],
            "InferenceUrl": url + "/inference",
            "ChatCompletionsUrl": url + "/inference/v1/chat/completions",
            "MessagesUrl": url + "/inference/v1/messages",
            "TargetId": tgt["targetId"]}


def delete_all(gateway_id):
    if not gateway_id or gateway_id == "none":
        return
    for attempt in range(4):
        try:
            for t in acc.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", []):
                acc.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=t["targetId"])
                time.sleep(3)
            acc.delete_gateway(gatewayIdentifier=gateway_id)
            return
        except acc.exceptions.ResourceNotFoundException:
            return
        except Exception as e:  # noqa: BLE001
            if attempt < 3:
                time.sleep(10)
                continue
            # never block a stack delete; an orphaned gateway is recoverable
            print(f"WARNING: gateway {gateway_id} not deleted: {e}")


def handler(event, context):
    try:
        req, props = event["RequestType"], event.get("ResourceProperties", {})
        if req == "Create":
            send(event, context, "SUCCESS", create_all(props))
        elif req == "Update":
            data = create_all(props)          # create new, then CFN deletes old via Delete on old id
            send(event, context, "SUCCESS", data)
        else:
            delete_all(event.get("PhysicalResourceId", ""))
            send(event, context, "SUCCESS", {})
    except Exception as e:  # noqa: BLE001 - must always signal CFN
        send(event, context, "FAILED", reason=str(e)[:400])
