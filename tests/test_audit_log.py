"""Audit log captures every request with timestamp + method + path + status."""

import re

ISO_TS = re.compile(r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\]")


def test_all_requests_logged(bterminal_process):
    c = bterminal_process.http_client

    # 10 known requests covering 200 / 404 / 401 paths.
    expected = [
        ("GET",  "/api/health",                       200),
        ("GET",  "/api/state",                        200),
        ("GET",  "/api/tabs",                         200),
        ("GET",  "/api/plugins",                      200),
        ("GET",  "/api/sidecars",                     200),
        ("GET",  "/api/window/screenshot",            200),
        ("GET",  "/api/debug/log",                    200),
        ("POST", "/api/quit",                         400),  # missing ?confirm
        ("GET",  "/api/this-path-does-not-exist",     404),
        ("POST", "/api/sidecars/no-such-name/start",  404),
    ]

    for method, path, _expected_status in expected:
        c.request(method, path)

    log_lines = c.get("/api/debug/log").json()["lines"]

    # Every line must start with an ISO timestamp.
    assert all(ISO_TS.match(line) for line in log_lines if line), (
        "audit log lines must start with [ISO-timestamp]"
    )

    # Every expected (method, path, status) must appear at least once in
    # the tail of the log (last 200 lines is the audit endpoint window).
    for method, path, status in expected:
        needle_prefix = f"{method} {path} -> {status}"
        assert any(needle_prefix in line for line in log_lines), (
            f"audit log missing entry: {needle_prefix}\n"
            f"last 5 lines:\n" + "\n".join(log_lines[-5:])
        )
