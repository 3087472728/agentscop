# -*- coding: utf-8 -*-
"""Phase 3 auth probe: no-creds / wrong-password / correct-password.

密码来源：AGENTSCOPE_PASSWORD（env/.env）或 getpass 交互——
roles.json 里只有哈希，无法（也不应）从配置还原明文。
"""
import base64
import sys

sys.path.insert(0, "scripts")
import urllib.request
import urllib.error

from common import (  # noqa: E402
    BASE_URL,
    admin_password,
    auth_headers,
    http_ssl_context,
)


def probe(label, headers):
    req = urllib.request.Request(
        f"{BASE_URL}/agent/", headers=headers
    )
    try:
        with urllib.request.urlopen(
            req, timeout=10, context=http_ssl_context()
        ) as r:
            print(label, "->", r.status)
            return r.status
    except urllib.error.HTTPError as e:
        print(label, "->", e.code)
        return e.code


probe("no auth       ", {})
probe("wrong password", {
    "Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode(),
    "X-User-ID": "admin",
})
token = base64.b64encode(f"admin:{admin_password()}".encode()).decode()
code = probe("admin+correct ", {"Authorization": "Basic " + token, "X-User-ID": "admin"})
sys.exit(0 if code == 200 else 1)
