#!/usr/bin/env python3
"""Hit a running Between site and walk the two-person path.

Usage:
  python3 deploy/gcp/smoke_test.py --base-url https://between.example.com --env deploy/gcp/.env
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import ssl
import struct
import sys
import uuid
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


def load_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def opener() -> tuple[urllib.request.OpenerDirector, CookieJar]:
    jar = CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        _NoRedirect,
    ), jar


def request(op: urllib.request.OpenerDirector, method: str, url: str, data: dict | None = None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    try:
        return op.open(req, timeout=30)
    except urllib.error.HTTPError as exc:
        return exc


def cookie_header(jar: CookieJar) -> str:
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in jar)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL {message}")
    print(f"ok   {message}")


def ws_connect(base_url: str, path: str, cookies: str, timeout: float = 15):
    parsed = urllib.parse.urlparse(base_url)
    host = parsed.hostname or ""
    secure = parsed.scheme == "https"
    port = parsed.port or (443 if secure else 80)
    raw = socket.create_connection((host, port), timeout)
    sock = raw
    if secure:
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode()
    host_header = parsed.netloc
    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Cookie: {cookies}\r\n"
        "\r\n"
    )
    sock.sendall(handshake.encode())
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0]
    if b" 101 " not in status:
        raise SystemExit(f"FAIL websocket upgrade: {status!r}")
    return sock, rest


def ws_send(sock, payload: dict) -> None:
    raw = json.dumps(payload).encode()
    mask = os.urandom(4)
    header = bytearray([0x81])
    length = len(raw)
    if length < 126:
        header.append(0x80 | length)
    else:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    header.extend(mask)
    sock.sendall(bytes(header) + bytes(byte ^ mask[i % 4] for i, byte in enumerate(raw)))


def ws_recv(sock, pending: bytes, timeout: float = 15) -> tuple[dict | int, bytes]:
    sock.settimeout(timeout)
    buf = pending
    while len(buf) < 2:
        chunk = sock.recv(4096)
        if not chunk:
            raise SystemExit("FAIL websocket closed before a frame")
        buf += chunk
    first, second = buf[0], buf[1]
    offset = 2
    opcode = first & 0x0F
    masked = second & 0x80
    length = second & 0x7F
    if length == 126:
        while len(buf) < offset + 2:
            buf += sock.recv(4096)
        length = struct.unpack("!H", buf[offset : offset + 2])[0]
        offset += 2
    mask = b""
    if masked:
        while len(buf) < offset + 4:
            buf += sock.recv(4096)
        mask = buf[offset : offset + 4]
        offset += 4
    while len(buf) < offset + length:
        buf += sock.recv(4096)
    payload = buf[offset : offset + length]
    rest = buf[offset + length :]
    if masked:
        payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    if opcode == 9:
        pong = bytearray([0x8A, 0x80 | len(payload)])
        pong_mask = os.urandom(4)
        pong.extend(pong_mask)
        pong.extend(byte ^ pong_mask[i % 4] for i, byte in enumerate(payload))
        sock.sendall(pong)
        return ws_recv(sock, rest, timeout)
    if opcode == 8:
        code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 0
        return code, rest
    if opcode != 1:
        return ws_recv(sock, rest, timeout)
    return json.loads(payload.decode()), rest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--env", required=True)
    args = parser.parse_args()
    env = load_env(args.env)
    base = args.base_url.rstrip("/")
    chris_name = env.get("USER1_NAME", "chris")
    karin_name = env.get("USER2_NAME", "karin")
    chris_password = env["USER1_PASSWORD"]
    karin_password = env["USER2_PASSWORD"]
    marker = uuid.uuid4().hex[:8]
    secret = f"deploy-check-body-only-chris-should-see-this-before-open-{marker}"
    title = f"Deploy check {marker}"
    line = f"deploy-check-chat-line-{marker}"
    topic_id = ""
    chris: urllib.request.OpenerDirector | None = None
    karin: urllib.request.OpenerDirector | None = None
    karin_sock = None
    chris_sock = None
    try:
        health_op, _ = opener()
        health = request(health_op, "GET", f"{base}/health")
        body = health.read().decode()
        check(health.status == 200 and '"ok":true' in body.replace(" ", ""), "/health")

        chris, chris_jar = opener()
        bad = request(chris, "POST", f"{base}/login", {"username": chris_name, "password": "nope"})
        check(bad.status == 401, "wrong password is rejected")

        chris, chris_jar = opener()
        logged_in = request(chris, "POST", f"{base}/login", {"username": chris_name, "password": chris_password})
        check(logged_in.status == 303, f"login {chris_name}")
        created = request(chris, "POST", f"{base}/topics", {"title": title, "prompt": secret})
        location = created.headers.get("Location", "")
        topic_id = location.rstrip("/").rsplit("/", 1)[-1]
        check(created.status == 303 and topic_id.isdigit(), "topic kept on the desk")

        karin, karin_jar = opener()
        karin_login = request(karin, "POST", f"{base}/login", {"username": karin_name, "password": karin_password})
        check(karin_login.status == 303, f"login {karin_name}")
        home = request(karin, "GET", f"{base}/").read().decode()
        check(secret not in home and title not in home, "private topic is invisible")

        offered = request(chris, "POST", f"{base}/topics/{topic_id}/offer")
        check(offered.status == 303, "offer sent")
        sealed = request(karin, "GET", f"{base}/topics/{topic_id}").read().decode()
        check(title in sealed and secret not in sealed, "offer shows the title and hides the body")

        opened = request(karin, "POST", f"{base}/topics/{topic_id}/accept")
        check(opened.status == 303, "offer opened")
        shared = request(karin, "GET", f"{base}/topics/{topic_id}").read().decode()
        check(secret in shared, "opened topic shows the body")

        karin_sock, karin_rest = ws_connect(base, f"/ws/topics/{topic_id}", cookie_header(karin_jar))
        chris_sock, chris_rest = ws_connect(base, f"/ws/topics/{topic_id}", cookie_header(chris_jar))
        presence, karin_rest = ws_recv(karin_sock, karin_rest)
        check(isinstance(presence, dict) and presence.get("type") == "presence", "chat presence")
        _, chris_rest = ws_recv(chris_sock, chris_rest)
        ws_send(chris_sock, {"type": "chat", "body": line})
        seen = None
        for _ in range(4):
            message, karin_rest = ws_recv(karin_sock, karin_rest)
            if isinstance(message, dict) and message.get("body") == line:
                seen = message
                break
        check(seen is not None, "chat line arrives")

        revoked = request(chris, "POST", f"{base}/topics/{topic_id}/revoke")
        check(revoked.status == 303, "topic pulled back")
        karin_sock.settimeout(0.2)
        try:
            while True:
                extra = karin_sock.recv(4096)
                if not extra:
                    break
                karin_rest += extra
        except (TimeoutError, socket.timeout):
            pass
        karin_sock.settimeout(15)
        ws_send(karin_sock, {"type": "chat", "body": "after revoke"})
        closed = None
        for _ in range(6):
            message, karin_rest = ws_recv(karin_sock, karin_rest)
            if isinstance(message, int):
                closed = message
                break
        check(closed == 4404, "chat closes after revoke")
        hidden = request(karin, "GET", f"{base}/topics/{topic_id}")
        check(hidden.status in {302, 303} or secret not in hidden.read().decode(), "body is hidden again")
        print("Between is up.")
    finally:
        if karin_sock is not None:
            karin_sock.close()
        if chris_sock is not None:
            chris_sock.close()
        if chris is not None and topic_id:
            try:
                request(chris, "POST", f"{base}/topics/{topic_id}/revoke")
                request(chris, "POST", f"{base}/topics/{topic_id}/delete")
            except OSError:
                pass


if __name__ == "__main__":
    try:
        main()
    except OSError as exc:
        sys.exit(f"FAIL {exc}")
