#!/usr/bin/env python3
"""E2E smoke test for the per-channel audit toggle in new-api-audit.

Starts two local services:
- HMAC-verifying audit receiver on :18000 (pretends to be token-audit)
- Fake OpenAI-compatible upstream on :18900 (returns a canned completion)

Then drives new-api (on :13000) through setup/login/token/channel creation,
sends relay requests through channel A (audit on) and channel B (audit off),
and asserts only channel A produced signed request/usage audit events.
"""
import hashlib
import hmac
import json
import http.server
import threading
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:13000"
SECRET = b"smoke-secret-12345"
FAKE_UPSTREAM = "http://host.docker.internal:18900"

audit_events = []  # (path, body_bytes, headers)

class AuditReceiver(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        audit_events.append((self.path, body, dict(self.headers)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args):
        pass

class FakeUpstream(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        resp = {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "gpt-4o-mini",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "smoke ok"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass

def start_server(handler, port):
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

class Client:
    def __init__(self):
        self.cookies = {}
        self.access_token = None

    def req(self, method, path, body=None, token=None, headers=None):
        h = {"Content-Type": "application/json"}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if token:
            h["Authorization"] = f"Bearer {token}"
        elif self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        if headers:
            h.update(headers)
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
        try:
            resp = urllib.request.urlopen(r, timeout=30)
        except urllib.error.HTTPError as e:
            resp = e
        for sc in resp.headers.get_all("Set-Cookie") or []:
            kv = sc.split(";")[0]
            k, _, v = kv.partition("=")
            self.cookies[k.strip()] = v.strip()
        raw = resp.read()
        try:
            return resp.status, json.loads(raw)
        except Exception:
            return resp.status, raw.decode(errors="replace")

def wait_port_ready(url, tries=60):
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                r.read()
                return True
        except Exception:
            time.sleep(1)
    return False

def main():
    start_server(AuditReceiver, 18000)
    start_server(FakeUpstream, 18900)
    assert wait_port_ready(BASE + "/api/status"), "new-api did not come up"
    print("[1] new-api is up")

    c = Client()
    _, setup = c.req("GET", "/api/setup")
    if not setup.get("data", {}).get("setup") and not setup.get("data", {}).get("root_init"):
        st, r = c.req("POST", "/api/setup", {
            "username": "root", "password": "smoketest123",
            "ConfirmPassword": "smoketest123",
            "SelfUseModeEnabled": False, "DemoSiteEnabled": False,
        })
        assert st == 200 and r.get("success"), f"setup failed: {r}"
        print("[2] setup done")
    st, r = c.req("POST", "/api/user/login", {"username": "root", "password": "smoketest123"})
    assert r.get("success"), f"login failed: {r}"
    c.access_token = r["data"]["access_token"]
    print("[3] login ok")

    st, r = c.req("POST", "/api/token/", {
        "name": "smoke", "remain_quota": 500000, "expired_time": -1,
        "unlimited_quota": True, "model_limits_enabled": False,
        "model_limits": "", "allow_ips": "", "group": "",
    })
    assert r.get("success"), f"token create failed: {r}"
    st, r = c.req("GET", "/api/token/?p=1&size=10")
    token_id = next(t["id"] for t in r["data"]["items"] if t["name"] == "smoke")
    st, r = c.req("POST", f"/api/token/{token_id}/key", {})
    assert r.get("success"), f"token key fetch failed: {r}"
    token_key = r["data"] if isinstance(r["data"], str) else r["data"].get("key")
    if not token_key.startswith("sk-"):
        token_key = "sk-" + token_key
    print("[4] token created")

    setting_on = json.dumps({"audit_enabled": True})
    setting_off = json.dumps({"audit_enabled": False})
    st, r = c.req("POST", "/api/channel/", {
        "mode": "single", "channel": {
            "type": 1, "name": "audit-on", "key": "dummy-upstream-key",
            "base_url": FAKE_UPSTREAM, "models": "gpt-4o-mini",
            "group": "default", "groups": ["default"], "priority": 10,
            "setting": setting_on, "status": 1, "other": "", "model_mapping": "",
            "test_model": "gpt-4o-mini", "tag": "", "remark": "",
            "param_override": "", "header_override": "",
        },
    })
    assert r.get("success"), f"channel A create failed: {r}"
    st, r = c.req("POST", "/api/channel/", {
        "mode": "single", "channel": {
            "type": 1, "name": "audit-off", "key": "dummy-upstream-key",
            "base_url": FAKE_UPSTREAM, "models": "gpt-4o-mini",
            "group": "default", "groups": ["default"], "priority": 0,
            "setting": setting_off, "status": 1, "other": "", "model_mapping": "",
            "test_model": "gpt-4o-mini", "tag": "", "remark": "",
            "param_override": "", "header_override": "",
        },
    })
    assert r.get("success"), f"channel B create failed: {r}"
    print("[5] channels created (A: audit on/priority 10, B: audit off/priority 0)")

    req_body = {"model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "audit smoke test 你好 world"}]}
    for i in range(2):
        st, r = c.req("POST", "/v1/chat/completions", req_body, token=token_key)
        assert st == 200 and r.get("choices"), f"relay failed ({i}): {st} {r}"
    print("[6] 2 relay requests routed to channel A (higher priority) OK")

    time.sleep(2)
    req_events = [e for e in audit_events if e[0] == "/internal/new-api/audit/request"]
    usage_events = [e for e in audit_events if e[0] == "/internal/new-api/audit/usage"]
    assert len(audit_events) == len(req_events) + len(usage_events), "unexpected audit paths"

    for path, body, headers in audit_events:
        ts = headers.get("X-Audit-Timestamp")
        sig = headers.get("X-Audit-Signature")
        mac = hmac.new(SECRET, ts.encode() + b"." + body, hashlib.sha256)
        assert hmac.compare_digest(sig, mac.hexdigest()), f"bad HMAC signature on {path}"

    assert len(req_events) == 2, f"expected 2 request events, got {len(req_events)}"
    assert len(usage_events) == 2, f"expected 2 usage events, got {len(usage_events)}"
    for _, body, _ in usage_events:
        ev = json.loads(body)
        assert ev["channel_id"] == 1, f"usage event from wrong channel: {ev['channel_id']}"
        assert ev["prompt_tokens"] == 10 and ev["completion_tokens"] == 5
    for _, body, _ in req_events:
        ev = json.loads(body)
        assert "audit smoke test" in ev["prompt_text"], f"prompt text missing: {ev}"
    print("[7] HMAC signatures valid; 2 request + 2 usage events, all from channel 1 (audit on)")
    print("[8] channel 2 (audit off) produced 0 events — per-channel filtering works")
    print("SMOKE TEST PASSED")

if __name__ == "__main__":
    main()
