"""safe_fetch refuses local, private and metadata addresses - in the link, behind a DNS name and
behind a redirect - and connects only to the address it checked. A fake resolver and a mock
transport stand in for the network; the offline guard fails any real attempt."""
import os

import pytest
from httpx import ConnectError, MockTransport, Response

from companion.engine.net import MAX_REDIRECTS, BlockedAddressError, FetchError, blocked_reason, safe_fetch, safe_read

PUBLIC = "93.184.216.34"
BAD_LINKS = ["http://127.0.0.1/a.mp3", "http://10.0.0.5/a.mp3",
             "http://169.254.169.254/latest/meta-data/", "http://[::1]/a.mp3"]
BAD_IPS = ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"]


class DNS:
    def __init__(self, table=None):
        self.table = {"podcast.example": [PUBLIC], **(table or {})}
        self.calls = []

    def __call__(self, host, port):
        self.calls.append(host)
        return list(self.table[host])


class Server:
    """Answers by (Host header, path) and records every request it receives."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.seen = []

    def __call__(self, request):
        self.seen.append({"ip": request.url.host, "host": request.headers["host"], "path": request.url.path,
                          "sni": request.extensions.get("sni_hostname")})
        answer = self.routes.get((request.headers["host"], request.url.path), Response(404))
        return answer(request) if callable(answer) else answer

    @property
    def transport(self):
        return MockTransport(self)


def redirect(location):
    return Response(302, headers={"Location": location})


def fetch(url, tmp_path, server, dns, **kwargs):
    return safe_fetch(url, tmp_path / "out.mp3", resolver=dns, transport=server.transport, **kwargs)


@pytest.mark.parametrize("url", BAD_LINKS)
def test_refuses_private_addresses_in_the_link(url, tmp_path):
    server, dns = Server(), DNS()
    with pytest.raises(BlockedAddressError, match="Refused to download"):
        fetch(url, tmp_path, server, dns)
    assert server.seen == [] and dns.calls == [] and os.listdir(tmp_path) == []


@pytest.mark.parametrize("ip", BAD_IPS)
def test_refuses_a_name_that_resolves_to_a_private_address(ip, tmp_path):
    server, dns = Server(), DNS({"inside.example": [ip]})
    with pytest.raises(BlockedAddressError, match="inside.example resolves to"):
        fetch("https://inside.example/feed.xml", tmp_path, server, dns)
    assert server.seen == []


@pytest.mark.parametrize("target", BAD_LINKS + ["http://inside.example/x.mp3"])
def test_refuses_a_redirect_to_a_private_address(target, tmp_path):
    server = Server({("podcast.example", "/ep.mp3"): redirect(target)})
    dns = DNS({"inside.example": ["10.0.0.5"]})
    with pytest.raises(BlockedAddressError):
        fetch("https://podcast.example/ep.mp3", tmp_path, server, dns)
    assert [hit["path"] for hit in server.seen] == ["/ep.mp3"]     # the second hop never happened
    assert not (tmp_path / "out.mp3").exists()


@pytest.mark.parametrize("url", ["http://127.0.0.1/a.mp3", "http://10.0.0.5/a.mp3", "http://[::1]/a.mp3",
                                 "http://169.254.10.20/a.mp3", "http://100.101.102.103/a.mp3"])
def test_allow_private_opens_your_own_network(url, tmp_path):
    host = url.split("/")[2]
    server = Server({(host, "/a.mp3"): Response(200, content=b"audio bytes")})
    got = fetch(url, tmp_path, server, DNS(), allow_private=True)
    assert (tmp_path / "out.mp3").read_bytes() == b"audio bytes" and got.size == 11


def test_allow_private_follows_a_redirect_into_the_lan(tmp_path):
    server = Server({("podcast.example", "/ep.mp3"): redirect("http://nas.lan/ep.mp3"),
                     ("nas.lan", "/ep.mp3"): Response(200, content=b"lan audio")})
    got = fetch("https://podcast.example/ep.mp3", tmp_path, server, DNS({"nas.lan": ["192.168.1.20"]}),
                allow_private=True)
    assert got.url == "http://nas.lan/ep.mp3" and (tmp_path / "out.mp3").read_bytes() == b"lan audio"


@pytest.mark.parametrize("url", ["http://169.254.169.254/latest/meta-data/", "http://[fd00:ec2::254]/",
                                 "http://100.100.100.200/latest/meta-data/"])
def test_cloud_metadata_stays_refused_even_with_allow_private(url, tmp_path):
    server = Server()
    with pytest.raises(BlockedAddressError, match="cloud metadata"):
        fetch(url, tmp_path, server, DNS(), allow_private=True)
    assert server.seen == []


def test_connects_to_the_address_it_checked(tmp_path):
    server = Server({("podcast.example", "/ep.mp3"): Response(200, content=b"x" * 2048,
                                                               headers={"Content-Type": "audio/mpeg"})})
    dns = DNS()
    got = fetch("https://podcast.example/ep.mp3", tmp_path, server, dns)
    assert server.seen == [{"ip": PUBLIC, "host": "podcast.example", "path": "/ep.mp3",
                            "sni": "podcast.example"}]
    assert dns.calls == ["podcast.example"]                          # one lookup, no second one
    assert (got.size, got.content_type, got.url) == (2048, "audio/mpeg", "https://podcast.example/ep.mp3")
    assert os.listdir(tmp_path) == ["out.mp3"]                       # no temp file left behind


def test_every_dns_answer_must_pass(tmp_path):
    server = Server()
    with pytest.raises(BlockedAddressError):
        fetch("https://mixed.example/ep.mp3", tmp_path, server, DNS({"mixed.example": [PUBLIC, "10.0.0.5"]}))
    assert server.seen == []


def test_a_failed_connection_falls_back_to_the_next_checked_address(tmp_path):
    def handler(request):
        if request.url.host == "2606:2800:220:1::1":
            raise ConnectError("no IPv6 route", request=request)
        return Response(200, content=b"via ipv4")
    got = safe_fetch("https://dual.example/ep.mp3", tmp_path / "out.mp3", transport=MockTransport(handler),
                     resolver=DNS({"dual.example": ["2606:2800:220:1::1", PUBLIC]}))
    assert got.size == 8


@pytest.mark.parametrize("ip", ["::ffff:127.0.0.1", "64:ff9b::a00:5", "2002:a00:5::1", "fe80::1", "fc00::1",
                                "100.64.0.1", "0.0.0.0", "224.0.0.1", "240.0.0.1", "255.255.255.255",
                                "2001:db8::1", "192.0.0.192", "::", "ff02::1"])
def test_wrapped_and_special_addresses_are_refused(ip):
    assert blocked_reason(ip) is not None


@pytest.mark.parametrize("ip", ["8.8.8.8", PUBLIC, "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses_are_allowed(ip):
    assert blocked_reason(ip) is None


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://podcast.example/a.mp3", "gopher://podcast.example/",
                                 "javascript:alert(1)", "not a link", "http:///nohost"])
def test_only_http_and_https_links(url, tmp_path):
    with pytest.raises(BlockedAddressError):
        fetch(url, tmp_path, Server(), DNS())


def test_a_redirect_to_another_scheme_is_refused(tmp_path):
    server = Server({("podcast.example", "/ep.mp3"): redirect("file:///etc/passwd")})
    with pytest.raises(BlockedAddressError, match="Only http and https"):
        fetch("https://podcast.example/ep.mp3", tmp_path, server, DNS())


def test_size_cap_by_header_and_by_stream(tmp_path):
    server = Server({("podcast.example", "/big.mp3"): Response(200, content=b"x" * 5000),
                     ("podcast.example", "/stream.mp3"): lambda request: Response(
                         200, content=iter([b"x" * 600, b"x" * 600, b"x" * 600]))})
    for path in ("/big.mp3", "/stream.mp3"):
        with pytest.raises(FetchError, match="larger than"):
            fetch(f"https://podcast.example{path}", tmp_path, server, DNS(), max_bytes=1000)
        assert os.listdir(tmp_path) == []                            # no partial file either


def test_at_most_five_redirects(tmp_path):
    def hops(count):
        routes = {("podcast.example", f"/r{i}"): redirect(f"/r{i + 1}") for i in range(count)}
        routes[("podcast.example", f"/r{count}")] = Response(200, content=b"end")
        return Server(routes)
    dns = DNS()
    assert fetch("https://podcast.example/r0", tmp_path, hops(MAX_REDIRECTS), dns).size == 3
    assert dns.calls == ["podcast.example"] * (MAX_REDIRECTS + 1)    # every hop is checked again
    with pytest.raises(FetchError, match="Too many redirects"):
        fetch("https://podcast.example/r0", tmp_path, hops(MAX_REDIRECTS + 1), DNS())


def test_http_errors_and_safe_read(tmp_path):
    server = Server({("podcast.example", "/feed.xml"): Response(200, content=b"<rss/>")})
    assert safe_read("https://podcast.example/feed.xml", resolver=DNS(), transport=server.transport) == b"<rss/>"
    with pytest.raises(FetchError, match="answered 404"):
        fetch("https://podcast.example/missing.mp3", tmp_path, server, DNS())
