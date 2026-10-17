"""A small GitHub Actions API client on the standard library.

The one detail that matters: the log of a job is behind a redirect to a short-lived storage URL.
``urllib`` would send the ``Authorization`` header along to that other host. This client follows the
redirect itself and does not.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable

DEFAULT_API = "https://api.github.com"
FAILED = ("failure", "timed_out")


class ApiError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


class GitHub:
    def __init__(
        self,
        token: str,
        api_url: str | None = None,
        timeout: float = 30,
        attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        max_log_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        self.token = token
        self.api = (api_url or os.environ.get("GITHUB_API_URL") or DEFAULT_API).rstrip("/")
        self.timeout = timeout
        self.attempts = attempts
        self.sleep = sleep
        self.max_log_bytes = max_log_bytes
        self._opener = urllib.request.build_opener(_NoRedirect)

    # -- plumbing ---------------------------------------------------------------------------
    def _open(self, url: str, headers: dict[str, str]):
        req = urllib.request.Request(url, headers=headers)
        last: Exception | None = None
        for attempt in range(self.attempts):
            try:
                return self._opener.open(req, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308):
                    return exc  # the caller reads Location
                if exc.code >= 500 and attempt + 1 < self.attempts:
                    last = exc
                else:
                    raise self._explain(exc) from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
                if attempt + 1 >= self.attempts:
                    raise ApiError(f"cannot reach {url.split('?')[0]}: {exc}") from exc
            self.sleep(2**attempt)
        raise ApiError(f"giving up on {url}: {last}")

    @staticmethod
    def _explain(exc: urllib.error.HTTPError) -> ApiError:
        hint = {
            401: "the token was not accepted",
            403: "the token may lack `actions: read` (add it under `permissions:`), or the API rate "
            "limit was hit",
            404: "not found: wrong repository or run id, the token cannot see the repository, "
            "or the logs have expired",
        }.get(exc.code, "")
        return ApiError(f"GitHub API {exc.code} for {exc.url.split('?')[0]}" + (f" ({hint})" if hint else ""))

    def _api_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ci-failure-explainer",
        }

    def get_json(self, path: str):
        with self._open(self.api + path, self._api_headers()) as resp:
            return json.load(resp)

    # -- what the tool needs ----------------------------------------------------------------
    def run(self, repo: str, run_id: int | str) -> dict:
        return self.get_json(f"/repos/{repo}/actions/runs/{run_id}")

    def jobs(self, repo: str, run_id: int | str) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            data = self.get_json(
                f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100&page={page}&filter=latest"
            )
            batch = data.get("jobs", [])
            out.extend(batch)
            if len(batch) < 100:
                return out
            page += 1

    def job_log(self, repo: str, job_id: int | str) -> str:
        resp = self._open(f"{self.api}/repos/{repo}/actions/jobs/{job_id}/logs", self._api_headers())
        if getattr(resp, "code", 200) in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            if not location:
                raise ApiError("log redirect without a Location header")
            # no Authorization here: the storage URL carries its own signature
            resp = self._open(location, {"User-Agent": "ci-failure-explainer"})
        buf = bytearray()
        with resp:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > self.max_log_bytes:  # the failure is at the end: keep the tail
                    del buf[: len(buf) - self.max_log_bytes]
        return buf.decode("utf-8", errors="replace")


def select_failed(
    jobs: list[dict], include: list[str] | None = None, exclude: list[str] | None = None
) -> list[dict]:
    """Failed jobs, or the cancelled ones when nothing failed (cancelled siblings of a failed job
    in a matrix are noise, but a run whose only problem is a cancellation still deserves an answer)."""

    def wanted(job: dict) -> bool:
        name = job.get("name", "")
        if include and not any(s.lower() in name.lower() for s in include):
            return False
        return not (exclude and any(s.lower() in name.lower() for s in exclude))

    failed = [j for j in jobs if j.get("conclusion") in FAILED and wanted(j)]
    if failed:
        return failed
    return [j for j in jobs if j.get("conclusion") == "cancelled" and wanted(j)]
