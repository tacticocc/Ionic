"""LocalJudge, exercised against a real HTTP server.

The point of the local backend is that Ionic can run with zero traffic leaving
the machine. That promise is worth nothing if the code path has never executed,
so these tests stand up an actual OpenAI-compatible endpoint on localhost and
drive httpx through it for real.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ionic.compat import check_compatibility
from ionic.config import Config
from ionic.judge import JudgeUnavailable, LocalJudge, build_judge
from ionic.models import Severity, Verdict

VALID_PAYLOAD = {
    "assessment": "The sourcing guarantee is weaker than it looks.",
    "findings": [
        {
            "kind": "guarantee_weakened",
            "severity": "high",
            "summary": "Sourcing became best-effort",
            "detail": "'always' became 'when available'.",
            "affected_contract": "researcher",
            "evidence": ["always cite -> cite when available"],
            "recommendation": "Restore the absolute wording.",
        }
    ],
}


class _Handler(BaseHTTPRequestHandler):
    """Serves whatever the test told it to, and records the request."""

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        self.server.requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": json.loads(raw)}
        )

        status, body = self.server.responder()
        payload = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep pytest output clean
        pass


class StubServer:
    def __init__(self, responder):
        self.httpd = HTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.responder = responder
        self.httpd.requests = []
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/v1"

    @property
    def requests(self) -> list[dict]:
        return self.httpd.requests

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def chat_completion(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_build_judge_applies_configured_local_token_limit() -> None:
    judge = build_judge(
        Config(
            judge_provider="local",
            judge_model="qwen2.5-coder",
            judge_max_tokens=12_345,
        )
    )

    assert isinstance(judge, LocalJudge)
    assert judge.max_tokens == 12_345


@pytest.fixture
def serve():
    servers: list[StubServer] = []

    def _serve(responder):
        server = StubServer(responder)
        servers.append(server)
        return server

    yield _serve
    for server in servers:
        server.close()


# ---------------------------------------------------------------------------


def test_parses_a_well_formed_response(serve, planner, researcher):
    server = serve(lambda: (200, chat_completion(json.dumps(VALID_PAYLOAD))))
    judge = LocalJudge("qwen2.5-coder", base_url=server.base_url)

    result = judge.evaluate(planner, planner, [researcher], [])

    assert result.assessment.startswith("The sourcing guarantee")
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.severity is Severity.HIGH
    assert finding.origin == "semantic"
    assert finding.affected_contract == "researcher"
    assert finding.changed_contract == planner.id


def test_sends_a_request_a_local_server_can_actually_serve(serve, planner):
    server = serve(lambda: (200, chat_completion(json.dumps({"findings": []}))))
    LocalJudge("qwen2.5-coder", base_url=server.base_url).evaluate(planner, planner, [], [])

    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["path"] == "/v1/chat/completions"

    body = request["body"]
    assert body["model"] == "qwen2.5-coder"
    assert body["stream"] is False
    assert body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    # The contracts under review have to actually reach the model.
    assert planner.id in body["messages"][1]["content"]


def test_sends_the_api_key_when_one_is_configured(serve, planner):
    server = serve(lambda: (200, chat_completion(json.dumps({"findings": []}))))
    LocalJudge("m", base_url=server.base_url, api_key="secret-token").evaluate(
        planner, planner, [], []
    )
    headers = {k.lower(): v for k, v in server.requests[0]["headers"].items()}
    assert headers["authorization"] == "Bearer secret-token"


def test_omits_authorization_when_no_key_is_set(serve, planner):
    server = serve(lambda: (200, chat_completion(json.dumps({"findings": []}))))
    LocalJudge("m", base_url=server.base_url).evaluate(planner, planner, [], [])
    headers = {k.lower() for k in server.requests[0]["headers"]}
    assert "authorization" not in headers


def test_tolerates_a_model_that_wraps_json_in_a_code_fence(serve, planner, researcher):
    fenced = "Here you go:\n```json\n" + json.dumps(VALID_PAYLOAD) + "\n```\n"
    server = serve(lambda: (200, chat_completion(fenced)))
    result = LocalJudge("m", base_url=server.base_url).evaluate(planner, planner, [researcher], [])
    assert len(result.findings) == 1


def test_tolerates_prose_around_a_bare_object(serve, planner):
    noisy = "Sure! " + json.dumps({"assessment": "fine", "findings": []}) + " Hope that helps."
    server = serve(lambda: (200, chat_completion(noisy)))
    result = LocalJudge("m", base_url=server.base_url).evaluate(planner, planner, [], [])
    assert result.findings == []
    assert result.assessment == "fine"


def test_a_server_error_is_reported_not_raised_raw(serve, planner):
    server = serve(lambda: (500, {"error": "model not loaded"}))
    judge = LocalJudge("m", base_url=server.base_url)
    with pytest.raises(JudgeUnavailable, match="did not respond"):
        judge.evaluate(planner, planner, [], [])


def test_an_unexpected_response_shape_is_reported_clearly(serve, planner):
    server = serve(lambda: (200, {"unexpected": "shape"}))
    judge = LocalJudge("m", base_url=server.base_url)
    with pytest.raises(JudgeUnavailable, match="unexpected response shape"):
        judge.evaluate(planner, planner, [], [])


def test_non_json_content_is_reported_clearly(serve, planner):
    server = serve(lambda: (200, chat_completion("I am a small model and I have opinions.")))
    judge = LocalJudge("m", base_url=server.base_url)
    with pytest.raises(JudgeUnavailable, match="did not return JSON"):
        judge.evaluate(planner, planner, [], [])


def test_an_unreachable_endpoint_is_reported_clearly(planner):
    # Port 1 is reserved and nothing will be listening.
    judge = LocalJudge("m", base_url="http://127.0.0.1:1/v1", timeout=2.0)
    with pytest.raises(JudgeUnavailable, match="did not respond"):
        judge.evaluate(planner, planner, [], [])


def test_a_trailing_slash_in_base_url_does_not_double_up(serve, planner):
    server = serve(lambda: (200, chat_completion(json.dumps({"findings": []}))))
    LocalJudge("m", base_url=server.base_url + "/").evaluate(planner, planner, [], [])
    assert server.requests[0]["path"] == "/v1/chat/completions"


def test_end_to_end_through_check_compatibility(serve, planner, researcher):
    """The whole point: a local model can block a merge with no network egress."""
    server = serve(lambda: (200, chat_completion(json.dumps(VALID_PAYLOAD))))
    judge = LocalJudge("qwen2.5-coder", base_url=server.base_url)

    report = check_compatibility(
        planner,
        planner.revise(version="1.1.0"),
        [researcher],
        judge=judge,
        fail_on=Severity.HIGH,
    )

    assert report.verdict is Verdict.REQUEST_CHANGES
    assert report.judge.enabled is True
    assert report.judge.provider == "local"
    assert report.judge.model == "qwen2.5-coder"
    assert [f.kind for f in report.findings if f.origin == "semantic"] == ["guarantee_weakened"]


def test_a_broken_local_server_degrades_to_structural_only(serve, planner, researcher):
    server = serve(lambda: (500, {"error": "out of memory"}))
    report = check_compatibility(
        planner,
        planner.revise(version="1.1.0", tools=[]),
        [researcher],
        judge=LocalJudge("m", base_url=server.base_url),
    )
    # Structural analysis still fired and still blocks.
    assert report.verdict is Verdict.REQUEST_CHANGES
    assert report.judge.enabled is False
    assert "did not respond" in (report.judge.error or "")
