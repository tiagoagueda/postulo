"""One HTTP client for every plugin that talks to another service.

Plugins do not roll their own: timeouts, size limits, a user agent and the destination
policy come from here, and so does the one place the policy can be reasoned about.

Capture refuses private addresses outright, because the URL came from a stranger's page.
A connection is different: the destination is what the person typed, and self-hosted
services — a Paperless on the same LAN, a mail server in the same Compose network — are
exactly where private addresses live. So the *operator* decides, once, with
``POSTULO_CONNECTIONS_ALLOW_PRIVATE``. Off by default, and the check runs on every
request the client makes, redirects included, so a public hostname cannot bounce a plugin
onto the router's administration page.
"""

from __future__ import annotations

import contextlib
import contextvars
import time
import zlib
from collections.abc import Iterator

import httpcore
import httpx

from .public_addresses import USER_AGENT, UnsafeURL, public_addresses_for, validate_public_url

DEFAULT_TIMEOUT = 10.0
MAX_REDIRECTS = 3

#: What a capped download tells the server it may compress with, and all `read_body` unpacks.
#: httpx inflates each chunk whole before anything can count it, and would offer brotli and
#: zstd as well: 317 bytes of brotli arrive as 200 MB in one piece. gzip and deflate are
#: inflated by `read_body` instead, a bounded piece at a time (#321).
ACCEPT_ENCODING = "gzip, deflate"

#: The most one step of inflating may produce, and so the most a body limit can be overshot
#: by, whatever the compression ratio.
_PIECE = 64 * 1024


class DestinationRefused(Exception):
    """The address is private and the operator has not allowed private destinations."""


class BodyRefused(Exception):
    """A response body `read_body` stopped reading. Which subclass says why."""


class BodyTooLarge(BodyRefused):
    """Over the limit: declared so, or found so before more than one chunk past it was read."""


class BodyTooSlow(BodyRefused):
    """Still arriving when the time given to the whole download ran out."""


class BodyUnreadable(BodyRefused):
    """Compressed in a way nobody asked for, or not validly compressed at all."""


class BodyInAnotherCoding(BodyUnreadable):
    """Sent in a content coding other than the ones `ACCEPT_ENCODING` asks for.

    ``coding`` is what the server called it, for the caller's own sentence about it. Refused
    whatever it is: brotli and zstd are the bombs this module stopped asking for, and a
    value nobody has heard of says nothing about what the bytes are (#321).
    """

    def __init__(self, coding: str):
        super().__init__(f"The body is encoded as {coding!r}, which was not asked for.")
        self.coding = coding


def private_destinations_allowed() -> bool:
    """The operator's switch, as `core.destinations` reads it for every guard (#248)."""
    from postulo.core import destinations

    return destinations.private_allowed()


def check_destination(url: str) -> None:
    """Raise unless ``url`` may be reached under the instance's policy."""
    if private_destinations_allowed():
        return
    try:
        validate_public_url(url)
    except UnsafeURL as exc:
        raise DestinationRefused(
            f"{exc} Connections may only reach private or local addresses when the operator "
            "sets POSTULO_CONNECTIONS_ALLOW_PRIVATE=true."
        ) from exc


def _refused(exc: UnsafeURL) -> DestinationRefused:
    """The connection policy's wording for an address the public check turned down."""
    return DestinationRefused(
        f"{exc} Connections may only reach private or local addresses when the operator "
        "sets POSTULO_CONNECTIONS_ALLOW_PRIVATE=true."
    )


def _guard(request: httpx.Request) -> None:
    """Approve the destination, and then connect to the address that was approved.

    Checking and connecting have to be one act, which is rule 5 of `docs/THREAT-MODEL.md`.
    `check_destination` resolved the name and threw the answer away, and httpx then resolved
    it again to open the socket: a name with a one-second lifetime is free to answer publicly
    for the check and with `127.0.0.1` or a metadata address a moment later, and the check
    would have passed on an address nothing ever contacted.

    Where the operator has allowed private destinations there is nothing left to enforce --
    every address passes -- so the name is left for httpx to resolve as it always did. That
    keeps self-hosted setups working the way they do today, including the ones whose names
    resolve differently inside a Compose network.
    """
    if private_destinations_allowed():
        return
    original = request.headers.get("Host")
    try:
        addresses = public_addresses_for(str(request.url))
    except UnsafeURL as exc:
        raise _refused(exc) from exc
    _pin(request, addresses[0])
    if original:
        request.headers["Host"] = original


def approve_host(host: str) -> str:
    """The address to dial for a plugin that speaks something other than HTTP.

    A mailbox, a message queue, anything with a socket of its own: resolve the name, hold
    every address it answers with to the instance's policy, and hand back the one to connect
    to. Keep the *name* for TLS — the certificate is proved against what somebody typed, and
    a certificate checked against a number never matches.

    Raises :class:`DestinationRefused`, whose message is for the person who typed the host.
    """
    from postulo.core import destinations

    try:
        return str(destinations.approve(host, allow_private=private_destinations_allowed()))
    except (destinations.Refused, destinations.Unresolvable) as error:
        raise DestinationRefused(str(error)) from error


def _pin(request: httpx.Request, address) -> None:
    """Send this request to ``address``, while still speaking to the host it names.

    Rewriting the URL is what makes the connection go to the address that was actually
    approved. The ``Host`` header and the TLS server name keep the original hostname, so
    the site sees the request it expects and the certificate is checked against the name
    a person typed rather than against a number.
    """
    host = request.url.host
    if host == str(address):
        return
    request.extensions = {**request.extensions, "sni_hostname": host}
    request.url = request.url.copy_with(host=str(address))


def _public_only(request: httpx.Request) -> None:
    """Refuse anything not publicly routable, and connect to what was approved.

    ``check_destination`` exists for connections, where a private address is the whole
    point. Some things Postulo fetches are public by definition — a portfolio address a
    recruiter will click, a job posting from a stranger's page — and for those the
    operator's connection policy is not the question being asked.

    The hook runs on every request the client makes, redirects included, and each time it
    resolves the name, approves every address it answers with, and then pins the request
    to one of them. Checking and connecting have to be one act: two lookups leave a gap
    that a short-lived record can answer differently.
    """
    original = request.headers.get("Host")
    try:
        addresses = public_addresses_for(str(request.url))
    except UnsafeURL as exc:
        raise DestinationRefused(str(exc)) from exc
    _pin(request, addresses[0])
    if original:
        request.headers["Host"] = original


def _build(guard, timeout: float, kwargs: dict, *, bounded: bool = False) -> httpx.Client:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    hooks = kwargs.pop("event_hooks", {})
    hooks = {**hooks, "request": [guard, *hooks.get("request", [])]}
    kwargs.setdefault("follow_redirects", True)
    kwargs.setdefault("max_redirects", MAX_REDIRECTS)
    built = httpx.Client(timeout=timeout, headers=headers, event_hooks=hooks, **kwargs)
    if bounded:
        _hold_to_deadlines(built)
    return built


def client(*, timeout: float = DEFAULT_TIMEOUT, **kwargs) -> httpx.Client:
    """An ``httpx.Client`` with Postulo's defaults and the destination policy attached.

    Use it as a context manager. Extra keyword arguments go to ``httpx.Client``; a
    plugin needing basic auth, for instance, passes ``auth=``.
    """
    return _build(_guard, timeout, kwargs)


def public_only_client(*, timeout: float = DEFAULT_TIMEOUT, **kwargs) -> httpx.Client:
    """Like :func:`client`, but private addresses are refused however the instance is set.

    The hook runs on every request the client makes rather than once on the address it
    was handed, which is the part that matters: a public host is perfectly free to answer
    ``302 Location: http://127.0.0.1:9000/``, and a client following that redirect on its
    own would make the request before anything had a chance to object. It also pins each
    request to an address that passed, so the connection cannot land somewhere the check
    never saw.

    What it fetches comes from a stranger, so every wait on the network it makes inside an
    `until` block is cut to what is left of that block's deadline (#321).
    """
    return _build(_public_only, timeout, kwargs, bounded=True)


# ------------------------------------------------------------ a deadline for the whole download


def deadline_in(seconds: float) -> float:
    """The moment ``seconds`` from now, on the clock `read_body` and `until` hold a deadline to."""
    return time.monotonic() + seconds


def past(deadline: float) -> bool:
    return time.monotonic() >= deadline


#: The deadline of the download under way in this thread, if there is one: see `until`.
_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "postulo_download_deadline", default=None
)


@contextlib.contextmanager
def until(deadline: float) -> Iterator[None]:
    """Hold every wait on the network inside this block to ``deadline`` (from `deadline_in`).

    `read_body` checks the deadline for every chunk of a body, and cannot see what httpcore
    reads without handing anything up: the status line and the headers, a chunk's extension,
    the trailers after the last chunk. A server sending any of those a byte at a time, each
    just inside the client's timeout, could hold a worker for days (#321). So on a
    `public_only_client`, inside this block, every connect, read and write waits no longer
    than what is left, and not at all once nothing is: the download is over by the deadline,
    the name lookups aside, which the system's resolver bounds.
    """
    token = _deadline.set(deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def _left(timeout: float | None, expired: type[Exception]) -> float | None:
    """``timeout`` cut to what is left of the deadline under way; ``expired`` once none is."""
    deadline = _deadline.get()
    if deadline is None:
        return timeout
    left = deadline - time.monotonic()
    if left <= 0:
        raise expired("The time for this download ran out.")
    return left if timeout is None else min(timeout, left)


class _BoundedStream(httpcore.NetworkStream):
    """A connection whose every wait is cut to the deadline under way, if there is one."""

    def __init__(self, stream: httpcore.NetworkStream):
        self._stream = stream

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._stream.read(max_bytes, _left(timeout, httpcore.ReadTimeout))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._stream.write(buffer, _left(timeout, httpcore.WriteTimeout))

    def close(self) -> None:
        self._stream.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None) -> _BoundedStream:
        # One wait, not one per read: CPython holds a whole handshake to the socket's timeout.
        timeout = _left(timeout, httpcore.ConnectTimeout)
        return _BoundedStream(self._stream.start_tls(ssl_context, server_hostname, timeout))

    def get_extra_info(self, info: str):
        return self._stream.get_extra_info(info)


class _BoundedBackend(httpcore.NetworkBackend):
    """The backend a pool was given, handing out `_BoundedStream` connections."""

    def __init__(self, backend: httpcore.NetworkBackend):
        self._backend = backend

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        timeout = _left(timeout, httpcore.ConnectTimeout)
        return _BoundedStream(
            self._backend.connect_tcp(host, port, timeout, local_address, socket_options)
        )

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        timeout = _left(timeout, httpcore.ConnectTimeout)
        return _BoundedStream(self._backend.connect_unix_socket(path, timeout, socket_options))

    def sleep(self, seconds: float) -> None:
        # httpcore sleeps between the attempts of a transport built with `retries`; no
        # caller asks for any today, and one that did would otherwise sleep past the end.
        self._backend.sleep(_left(seconds, httpcore.ConnectTimeout))


def _hold_to_deadlines(client: httpx.Client) -> None:
    """Give every connection pool ``client`` has a `_BoundedBackend`.

    httpx builds its pools itself and takes no network backend, so the one each pool was given
    is wrapped where it is, before any connection exists: the client's own transport, and one
    for each proxy the environment names. A transport that is not httpx's own -- a test's
    `MockTransport` -- opens no connection and is left as it is, and so is a client that is
    a test's stand-in and has no transports to look at.

    The names are httpx's and httpcore's own rather than anything they promise. A pool is
    read without a fallback, so one that has moved fails here rather than going quietly
    unbounded. The client's own two names are read with one, for the tests' stand-in
    clients, so a client that renamed them would go unbounded in silence: `pyproject.toml`
    holds httpx to the minor these were read from, and the tests in
    `tests/security/test_outbound.py` that stall a real socket fail when they move.
    """
    transports = [getattr(client, "_transport", None), *getattr(client, "_mounts", {}).values()]
    for transport in transports:
        if isinstance(transport, httpx.HTTPTransport):
            pool = transport._pool
            pool._network_backend = _BoundedBackend(pool._network_backend)


# ------------------------------------------------------------ reading what comes back


def read_body(response: httpx.Response, *, limit: int, deadline: float) -> bytes:
    """The body of a *streamed* response, never holding more than ``limit`` and one chunk.

    Streamed, because ``client.get`` has read the whole body before it returns, so a size
    limit applied to what it hands back limits nothing: a hostile server, which is all a
    capture or a logo address needs to name, could make a worker hold whatever it liked
    (#321). So a declared ``Content-Length`` over the limit is refused before a byte is
    read, and otherwise reading stops at the first chunk that takes the total past it --
    whatever the header said.

    A timeout on the client bounds each wait for the network, not their sum, and a server
    sending a byte every few seconds never trips it. ``deadline`` (from `deadline_in`)
    bounds the sum: checked here for every chunk that arrives, compressed or not, and below
    the body by `until`, which the caller opens around the whole request. A wait `until` cut
    short is refused here as too slow, like a chunk that arrived late.

    gzip and deflate are inflated here, a piece of at most 64 KiB at a time, rather than by
    httpx, which inflates each chunk whole; ask for them with `ACCEPT_ENCODING`. Anything
    else is refused rather than unpacked or guessed at, as `BodyInAnotherCoding`.
    """
    declared = response.headers.get("Content-Length", "").strip()
    if declared.isdecimal() and int(declared) > limit:
        raise BodyTooLarge(f"The body is declared as {declared} bytes, over {limit}.")
    body, over = _read(response, limit, deadline)
    if over is not None:
        raise over
    return body


def read_start(response: httpx.Response, *, limit: int, deadline: float) -> tuple[bytes, bool]:
    """The first ``limit`` bytes of a streamed body, and whether it went on past them.

    `read_body` for a file whose beginning is worth having when the whole is too large --
    robots.txt, which a crawler is to read at least the first 500 KiB of (RFC 9309) -- so
    neither a declared length nor the body outgrowing the limit is a refusal here. No more
    of it is read, or held, than `read_body` would have; the deadline and the codings are
    the same.
    """
    body, over = _read(response, limit, deadline)
    return body, over is not None


def _read(
    response: httpx.Response, limit: int, deadline: float
) -> tuple[bytes, BodyTooLarge | None]:
    """At most ``limit`` bytes of the body, and the refusal it earned if there was more."""
    chunks = _decoded(response, limit, deadline)
    body = bytearray()
    if past(deadline):
        raise BodyTooSlow("The time for this download ran out before its body began.")
    try:
        for chunk in chunks:
            body += chunk
            if len(body) > limit:
                return bytes(body[:limit]), BodyTooLarge(f"The body grew past {limit} bytes.")
            if past(deadline):
                raise BodyTooSlow("The body was still arriving when its time ran out.")
    except BodyTooLarge as over:
        # What arrived, still compressed, outgrew the limit before what it inflates to did.
        return bytes(body), over
    except httpx.TimeoutException as error:
        if past(deadline):
            raise BodyTooSlow("The body was still arriving when its time ran out.") from error
        raise
    return bytes(body), None


def _decoded(response: httpx.Response, limit: int, deadline: float) -> Iterator[bytes]:
    """What the body stands for, a chunk at a time, whichever way it was sent."""
    # httpx joins a repeated header with a comma, so two codings are refused like any other
    # value that is not exactly one of these.
    coding = response.headers.get("Content-Encoding", "").strip().lower()
    if coding in ("", "identity"):
        # Nothing to inflate, so each chunk is what arrived.
        return response.iter_bytes()
    if coding in ("gzip", "x-gzip", "deflate"):
        return _inflated(response.iter_raw(), limit, deadline, bare=coding == "deflate")
    raise BodyInAnotherCoding(coding)


def _inflated(raw: Iterator[bytes], limit: int, deadline: float, *, bare: bool) -> Iterator[bytes]:
    """gzip or deflate, inflated no more than `_PIECE` bytes at a time.

    What arrives is held to the limit as well as what it inflates to, and to the deadline
    chunk by chunk: a gzip header's comment, an empty deflate block or anything after the end
    of the stream inflates to nothing, and a server sending those a byte at a time never
    reached the checks `read_body` makes on what comes out (#321). Reading stops where the
    compressed stream ends, so whatever a server sends after that is never read at all.

    ``bare``: ``Content-Encoding: deflate`` means zlib-wrapped deflate, and some servers send
    it without the wrapper. A stream whose first two bytes are not a zlib or gzip header is
    then read as bare deflate, as httpx reads it.
    """
    inflater = None
    start = b""
    received = 0
    try:
        for chunk in raw:
            if past(deadline):
                raise BodyTooSlow("The body was still arriving when its time ran out.")
            received += len(chunk)
            if received > limit:
                raise BodyTooLarge(f"The compressed body grew past {limit} bytes.")
            if inflater is None:
                # The first two bytes say which of the three it is.
                start += chunk
                if len(start) < 2:
                    continue
                inflater, chunk = _inflater(start, bare=bare), start
            yield from _pieces(inflater, chunk)
            if inflater.eof:
                return
        if inflater is None:
            # A stream of one byte or none: nothing to choose an inflater by, or to inflate.
            return
        # Whatever a stream that ended early left behind: a few bytes at most, since every
        # chunk above was inflated until it stopped filling the piece.
        tail = inflater.flush()
    except zlib.error as error:
        raise BodyUnreadable(f"The body is not valid gzip or deflate: {error}") from error
    if tail:
        yield tail


def _inflater(start: bytes, *, bare: bool):
    """An inflater for the stream that begins with ``start``, two bytes or more."""
    if bare:
        try:
            zlib.decompressobj(32 + zlib.MAX_WBITS).decompress(start[:2])
        except zlib.error:
            return zlib.decompressobj(-zlib.MAX_WBITS)
    # 32 + MAX_WBITS: a gzip header or a zlib one, whichever the stream starts with.
    return zlib.decompressobj(32 + zlib.MAX_WBITS)


def _pieces(inflater, data: bytes) -> Iterator[bytes]:
    """``data`` inflated, `_PIECE` bytes at most at a time."""
    while True:
        piece = inflater.decompress(data, _PIECE)
        if piece:
            yield piece
        data = inflater.unconsumed_tail
        # A piece short of the ceiling means the input ran out, not the room.
        if not data and len(piece) < _PIECE:
            return
