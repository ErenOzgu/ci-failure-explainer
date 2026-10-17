"""Local stand-ins for the GitHub API and the Anthropic Messages API.

Nothing in the test suite talks to the real services. The fakes have the shape of the documented
endpoints, including the detail that matters most for the client: a job log is served from a
second host behind a redirect.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Server:
    def __init__(self, handler: type[BaseHTTPRequestHandler]) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.owner = self  # type: ignore[attr-defined]
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.requests: list[dict] = []
        self.thread = threading.Thread(
            target=lambda: self.httpd.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def record(self, handler: BaseHTTPRequestHandler, body: bytes = b"") -> None:
        headers = {k.lower(): v for k, v in handler.headers.items()}
        self.requests.append({"path": handler.path, "headers": headers, "body": body})


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: ANN002
        pass


class FakeBlob(_Server):
    """The storage host that serves the log text. It must never see the API token."""

    def __init__(self, logs: dict[str, str]) -> None:
        self.logs = logs

        class H(_Quiet):
            def do_GET(self) -> None:  # noqa: N802
                owner = self.server.owner  # type: ignore[attr-defined]
                owner.record(self)
                job = self.path.rsplit("/", 1)[-1]
                text = owner.logs.get(job)
                if text is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = text.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        super().__init__(H)


class FakeGitHub(_Server):
    def __init__(self, token: str, jobs: list[dict], logs: dict[str, str], page_size: int = 100) -> None:
        self.token = token
        self.jobs = jobs
        self.blob = FakeBlob(logs)
        self.fail_next: list[int] = []  # status codes to answer before behaving
        owner_self = self

        class H(_Quiet):
            def do_GET(self) -> None:  # noqa: N802
                owner = self.server.owner  # type: ignore[attr-defined]
                owner.record(self)
                if owner.fail_next:
                    self.send_response(owner.fail_next.pop(0))
                    self.end_headers()
                    return
                if self.headers.get("Authorization") != f"Bearer {owner_self.token}":
                    self.send_response(401)
                    self.end_headers()
                    return
                path, _, query = self.path.partition("?")
                q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                parts = path.strip("/").split("/")
                # /repos/o/r/actions/runs/1/jobs
                if parts[-1] == "jobs" and parts[-3] == "runs":
                    page = int(q.get("page", "1"))
                    per = int(q.get("per_page", "100"))
                    chunk = owner.jobs[(page - 1) * per : page * per]
                    self._json({"total_count": len(owner.jobs), "jobs": chunk})
                elif parts[-1] == "logs" and parts[-3] == "jobs":
                    job_id = parts[-2]
                    if job_id not in owner.blob.logs:
                        self.send_response(404)
                        self.end_headers()
                        return
                    self.send_response(302)
                    self.send_header("Location", f"{owner.blob.url}/blob/{job_id}")
                    self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()

            def _json(self, obj: object) -> None:
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        super().__init__(H)

    def close(self) -> None:
        super().close()
        self.blob.close()


class FakeAnthropic(_Server):
    def __init__(self, reply: str = "", status: int = 200) -> None:
        self.reply = reply
        self.status = status

        class H(_Quiet):
            def do_POST(self) -> None:  # noqa: N802
                owner = self.server.owner  # type: ignore[attr-defined]
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.record(self, body)
                if owner.status != 200:
                    data = json.dumps(
                        {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
                    ).encode()
                    self.send_response(owner.status)
                else:
                    data = json.dumps(
                        {
                            "id": "msg_test",
                            "type": "message",
                            "role": "assistant",
                            "model": "fake",
                            "content": [{"type": "text", "text": owner.reply}],
                            "stop_reason": "end_turn",
                        }
                    ).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        super().__init__(H)

    def last_request_json(self) -> dict:
        return json.loads(self.requests[-1]["body"])
