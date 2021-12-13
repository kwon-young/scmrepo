import socket
import threading
from io import StringIO
from typing import Any, Dict, Iterator

import asyncssh
import paramiko
import pytest
from dulwich.contrib.test_paramiko_vendor import (
    CLIENT_KEY,
    PASSWORD,
    USER,
    Server,
)
from pytest_mock import MockerFixture

from scmrepo.git.backend.dulwich.asyncssh_vendor import AsyncSSHVendor

# pylint: disable=redefined-outer-name


@pytest.fixture
def ssh_conn(request: pytest.FixtureRequest) -> Iterator[Dict[str, Any]]:
    server = Server([])

    socket.setdefaulttimeout(10)
    request.addfinalizer(lambda: socket.setdefaulttimeout(None))

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    request.addfinalizer(sock.close)
    port = sock.getsockname()[1]

    conn_info = {"port": port, "server": server}

    def _run_server():
        try:
            conn, _ = sock.accept()
        except socket.error:
            return False
        server.transport = transport = paramiko.Transport(conn)
        request.addfinalizer(transport.close)
        host_key = paramiko.RSAKey.from_private_key(StringIO(CLIENT_KEY))
        transport.add_server_key(host_key)
        transport.start_server(server=server)

    thread = threading.Thread(target=_run_server)
    thread.start()
    yield conn_info


@pytest.fixture
def ssh_port(ssh_conn: Dict[str, Any]) -> int:
    return ssh_conn["port"]


@pytest.fixture
def server(ssh_conn: Dict[str, Any]) -> Server:
    return ssh_conn["server"]


def test_run_command_password(server: Server, ssh_port: int):
    vendor = AsyncSSHVendor()
    vendor.run_command(
        "127.0.0.1",
        "test_run_command_password",
        username=USER,
        port=ssh_port,
        password=PASSWORD,
    )

    assert b"test_run_command_password" in server.commands


def test_run_command_with_privkey(server: Server, ssh_port: int):
    key = asyncssh.import_private_key(CLIENT_KEY)

    vendor = AsyncSSHVendor()
    vendor.run_command(
        "127.0.0.1",
        "test_run_command_with_privkey",
        username=USER,
        port=ssh_port,
        key_filename=key,
    )

    assert b"test_run_command_with_privkey" in server.commands


def test_run_command_data_transfer(server: Server, ssh_port: int):
    vendor = AsyncSSHVendor()
    con = vendor.run_command(
        "127.0.0.1",
        "test_run_command_data_transfer",
        username=USER,
        port=ssh_port,
        password=PASSWORD,
    )

    assert b"test_run_command_data_transfer" in server.commands

    channel = server.transport.accept(5)  # type: ignore[attr-defined]
    channel.send(b"stdout\n")
    channel.send_stderr(b"stderr\n")
    channel.close()

    assert con.can_read()
    assert con.read(4096) == b"stdout\n"
    assert con.read_stderr(4096) == b"stderr\n"


@pytest.mark.parametrize(
    "algorithm", [b"ssh-rsa", b"rsa-sha2-256", b"rsa-sha2-512"]
)
def test_dulwich_github_compat(mocker: MockerFixture, algorithm: bytes):
    from asyncssh.misc import ProtocolError

    from scmrepo.git.backend.dulwich.asyncssh_vendor import (
        _process_public_key_ok_gh,
    )

    key_data = b"foo"
    auth = mocker.Mock(
        _keypair=mocker.Mock(algorithm=algorithm, public_data=key_data),
    )
    packet = mocker.Mock()

    with pytest.raises(ProtocolError):
        strings = iter((b"ed21556", key_data))
        packet.get_string = lambda: next(strings)
        _process_public_key_ok_gh(auth, None, None, packet)

    strings = iter((b"ssh-rsa", key_data))
    packet.get_string = lambda: next(strings)
    _process_public_key_ok_gh(auth, None, None, packet)
