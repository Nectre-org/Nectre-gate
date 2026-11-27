import pytest

import nectregate.server
from nectregate import cli


class _FakeServer:
    def __init__(self):
        self.calls = []

    def run(self, *a, **kw):
        self.calls.append((a, kw))


@pytest.fixture
def fake_server(monkeypatch):
    server = _FakeServer()
    monkeypatch.setattr(nectregate.server, "build_server", lambda reg: server)
    return server


def test_remote_http_bind_refused(tmp_path, fake_server):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--db", str(tmp_path / "r.db"), "serve", "--http", "--host", "0.0.0.0"])
    assert "--allow-remote" in str(exc.value.code)
    assert fake_server.calls == []


def test_remote_http_bind_with_opt_in(tmp_path, fake_server):
    cli.main(["--db", str(tmp_path / "r.db"), "serve", "--http", "--host", "0.0.0.0", "--allow-remote"])
    assert fake_server.calls[0][1]["host"] == "0.0.0.0"


def test_default_host_is_loopback(tmp_path, fake_server):
    cli.main(["--db", str(tmp_path / "r.db"), "serve", "--http"])
    assert fake_server.calls[0][1]["host"] == "127.0.0.1"


@pytest.mark.parametrize("host,expected", [("localhost", True), ("127.0.0.1", True), ("::1", True),
                                           ("0.0.0.0", False), ("::", False), ("example.com", False)])
def test_is_loopback(host, expected):
    assert cli._is_loopback(host) is expected
