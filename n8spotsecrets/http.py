"""Tiny stdlib HTTP layer: JSON GET/POST with a hard timeout, ArcGIS and Socrata helpers.

Every source goes through here so timeouts and the User-Agent are one decision.
No keys live in this file; callers pass them as query params.

0.2.4: from the Lambda, some connections to data.lacity.org never finish their TLS handshake
while others opened in the same second answer in under a second. The old code waited the whole
timeout on such a connection and then had no time left. Now the connect + handshake step gets
1.5 s, and a connection that fails there is opened again (up to 3 tries) inside the same timeout.
Nothing has been sent when that step fails, so trying again is always safe.

0.2.5: one report used to open about 20 separate connections to data.lacity.org, one per
question, and the city's front door drops some of them when it is busy. Requests to that host
now share a small set of connections that are kept open and reused (at most 8 at a time), so
a report needs a handful of handshakes instead of 20. net_stats() says how many were needed.

0.2.6: the first report after a quiet spell still opened 8 connections in the same instant and
the city dropped some of them (4 of 12 tries from a desktop PC, 1 from the Lambda). Now at most
2 connections are being opened at any moment, and prewarm() starts opening a few while the
address is still being looked up, so they are ready when the questions go out.

0.2.7: measured from a desktop PC, one connection at a time: 8 of 54 handshakes with the city
site never finished (on all three of its addresses), most others took about 0.3 s, and opening 8
at once was no worse than opening one at a time. So the limit of 2 is gone (it only made
requests queue behind a stuck handshake). Instead a handshake still running after half a second
gets a second one racing it, then a third; the first to finish is used and a late finisher is
kept as a spare. prewarm() now opens one connection per slot, and a request waits for a
connection that is already on its way rather than opening another.
"""
import http.client
import json
import queue
import select
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "n8spotsecrets/0.1 (+https://github.com/ctoxyz/n8spotsecrets)"
CONNECT_S = 1.5      # connect + TLS handshake, per try
MAX_TRIES = 3
_TL = threading.local()


class _ConnectFailed(Exception):
    """The connection was never established (not an OSError, so urllib passes it through untouched)."""


class _FastConnect(http.client.HTTPSConnection):
    def connect(self):
        full = self.timeout
        self.timeout = getattr(_TL, "connect_s", None) or full
        try:
            super().connect()
        except Exception as e:
            raise _ConnectFailed(str(e) or e.__class__.__name__) from None
        finally:
            self.timeout = full
        self.sock.settimeout(full)


class _Handler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_FastConnect, req, context=self._context)


_CTX = ssl.create_default_context()
_OPENER = urllib.request.build_opener(_Handler(context=_CTX))


class SourceError(Exception):
    pass


# ---------------------------------------------------------------- shared connections for the busy host
POOL_HOSTS = {"data.lacity.org": 8}     # host -> most connections open at once
IDLE_MAX_S = 20.0                       # an idle connection older than this is closed, not reused
HEDGE_S = 0.5                           # a handshake still running after this long gets another one racing it
ATTEMPT_S = 3.0                         # the longest any single handshake is given
PREWARM_S = 3.0                         # how long prewarm() keeps trying to open one connection
WARM_WAIT_S = 1.5                       # the longest a request waits for a connection prewarm() is still opening
_POOLS = {}
_POOLS_LOCK = threading.Lock()
_STATS = {}
_STATS_LOCK = threading.Lock()


class _Redirected(Exception):
    """The host answered 3xx; the caller falls back to the urllib path, which follows redirects."""


def _count(host, key, n=1):
    with _STATS_LOCK:
        d = _STATS.setdefault(host, {})
        d[key] = d.get(key, 0) + n


def net_stats(reset=False):
    """{host: {requests, handshakes, handshake_ms, reused, prewarmed, extra_tries, spares, connect_failed,
    stale_retried, waited_ms}} since the last reset. handshake_ms and waited_ms are sums."""
    with _STATS_LOCK:
        out = {h: dict(d) for h, d in _STATS.items()}
        if reset:
            _STATS.clear()
    return out


def _close(conn):
    try:
        conn.close()
    except Exception:
        pass


def _forget_tls_error():
    """A TLS read or write that fails can leave its error behind for this thread. Python then reports the next
    request that merely has to wait for its answer as that old error (seen on Python 3.13 with OpenSSL 3.5: one
    dead connection, then "Broken pipe" on every healthy one in the same thread). Starting a handshake on a
    connection to nowhere makes the TLS library drop what it was holding. About 0.1 ms; only called after a failure."""
    try:
        _CTX.wrap_bio(ssl.MemoryBIO(), ssl.MemoryBIO(), server_hostname="localhost").do_handshake()
    except Exception:
        pass


def _handshake(host, timeout):
    """One TCP connect + TLS handshake with a time limit. Returns the open connection or raises."""
    conn = http.client.HTTPSConnection(host, timeout=timeout, context=_CTX)
    try:
        conn.connect()
    except Exception:
        _close(conn)
        raise
    return conn


def _usable(conn, since):
    """An idle connection is reused only if it is young and the far end has not hung up."""
    sock = conn.sock
    if sock is None or time.time() - since > IDLE_MAX_S:
        return False
    try:
        if not select.select([sock], [], [], 0)[0]:
            return True
        # Something is waiting. Right after a TLS 1.3 handshake that is only the server's session
        # tickets; reading with a zero timeout lets the TLS layer swallow them. End-of-stream or
        # real bytes on an idle connection mean it must not be reused.
        sock.settimeout(0)
        try:
            sock.recv(1)
        except (ssl.SSLWantReadError, BlockingIOError):
            return True
        return False
    except Exception:
        _forget_tls_error()
        return False


class _HostPool:
    def __init__(self, host, size):
        self.host = host
        self.slots = threading.BoundedSemaphore(size)
        self.idle = queue.LifoQueue()
        self.lock = threading.Lock()
        self.warming = 0      # connections prewarm() is still opening
        self.claimed = 0      # requests waiting for one of those

    def take_slot(self, deadline):
        t0 = time.time()
        if not self.slots.acquire(timeout=max(0.05, deadline - t0 - 0.3)):
            raise SourceError("timed out waiting for a free connection to %s" % self.host)
        return t0

    def _idle_one(self):
        while True:
            try:
                conn, since = self.idle.get_nowait()
            except queue.Empty:
                return None
            if _usable(conn, since):
                return conn
            _close(conn)

    def obtain(self, deadline, fresh=False):
        """(connection, was_idle). An idle one if there is one; else one that prewarm() is already opening
        and no other request has spoken for; else a new one."""
        if not fresh:
            conn = self._idle_one()
            if conn is not None:
                return conn, True
            with self.lock:
                mine = self.warming > self.claimed
                if mine:
                    self.claimed += 1
            if mine:
                try:
                    end = min(deadline - 0.6, time.time() + WARM_WAIT_S)
                    while time.time() < end:
                        conn = self._idle_one()
                        if conn is not None:
                            return conn, True
                        with self.lock:
                            if self.warming <= 0:
                                break
                        time.sleep(0.02)
                    conn = self._idle_one()
                    if conn is not None:
                        return conn, True
                finally:
                    with self.lock:
                        self.claimed -= 1
        return self.open(deadline), False

    def give_back(self, conn, keep):
        if conn is not None:
            if keep and conn.sock is not None:
                self.idle.put((conn, time.time()))
            else:
                _close(conn)
        self.slots.release()

    def idle_count(self):
        """How many idle connections are still usable; the others are closed."""
        kept = []
        while True:
            try:
                conn, since = self.idle.get_nowait()
            except queue.Empty:
                break
            if _usable(conn, since):
                kept.append((conn, since))
            else:
                _close(conn)
        for item in reversed(kept):
            self.idle.put(item)
        return len(kept)

    def open(self, deadline):
        """A new connection, by racing handshakes: the first starts now, and while none has finished another
        starts every HEDGE_S (MAX_TRIES in all). The first to finish is returned; one that finishes later is
        kept as a spare; one that never finishes is dropped when its own time limit runs out."""
        box = queue.Queue()
        lock = threading.Lock()
        state = {"closed": False}

        def attempt():
            t0 = time.time()
            try:
                conn = _handshake(self.host, max(0.3, min(ATTEMPT_S, deadline - t0)))
            except Exception as e:
                _count(self.host, "connect_failed")
                box.put((None, (str(e) or e.__class__.__name__)[:110]))
                return
            _count(self.host, "handshakes")
            _count(self.host, "handshake_ms", int((time.time() - t0) * 1000))
            with lock:
                if not state["closed"]:
                    state["closed"] = True
                    box.put((conn, None))
                    return
            self.idle.put((conn, time.time()))
            _count(self.host, "spares")

        started = failed = 0
        next_at = 0.0
        try:
            while True:
                now = time.time()
                more = started < MAX_TRIES and (started == 0 or deadline - now > 0.4)
                if more and now >= next_at:
                    started += 1
                    if started > 1:
                        _count(self.host, "extra_tries")
                    threading.Thread(target=attempt, daemon=True).start()
                    next_at = now + HEDGE_S
                    more = started < MAX_TRIES
                until = min(deadline, next_at) if more else deadline
                try:
                    conn, err = box.get(timeout=max(0.01, until - time.time()))
                except queue.Empty:
                    if time.time() >= deadline - 0.02:
                        raise SourceError("connection failed after %d tr%s: The handshake operation timed out" % (
                            started, "y" if started == 1 else "ies")) from None
                    continue
                if conn is not None:
                    return conn
                failed += 1
                if failed >= started:
                    if started >= MAX_TRIES or deadline - time.time() < 0.6:
                        raise SourceError("connection failed after %d tr%s: %s" % (started, "y" if started == 1 else "ies", err)) from None
                    time.sleep(0.1)
                    next_at = 0.0
        finally:
            with lock:
                state["closed"] = True
            while True:
                try:
                    late = box.get_nowait()[0]
                except queue.Empty:
                    break
                if late is not None:
                    self.idle.put((late, time.time()))


def _pool(host):
    with _POOLS_LOCK:
        if host not in _POOLS:
            _POOLS[host] = _HostPool(host, POOL_HOSTS[host])
        return _POOLS[host]


def _proxied(hostname):
    try:
        return bool(urllib.request.getproxies().get("https")) and not urllib.request.proxy_bypass(hostname)
    except Exception:
        return False


def prewarm(host, n=None):
    """Start opening connections in the background (one per slot unless n says otherwise) and return at
    once with how many were started. Safe to call any time."""
    if TRANSPORT is not None or host not in POOL_HOSTS or _proxied(host.split(":")[0]):
        return 0
    pool = _pool(host)
    ready = pool.idle_count()
    with pool.lock:
        need = max(0, (POOL_HOSTS[host] if n is None else n) - ready - pool.warming)
        pool.warming += need

    def one():
        try:
            conn = pool.open(time.time() + PREWARM_S)
            pool.idle.put((conn, time.time()))
            _count(host, "prewarmed")
        except Exception:
            pass
        finally:
            with pool.lock:
                pool.warming -= 1

    for _ in range(need):
        threading.Thread(target=one, daemon=True).start()
    return need


def _pooled(host, target, headers, data, deadline):
    """One request over a shared connection. Returns the body, or raises SourceError / _Redirected."""
    pool = _pool(host)
    _count(host, "requests")
    t0 = pool.take_slot(deadline)
    conn = None
    reused = False
    keep = False
    first = True
    try:
        while True:
            if conn is None:
                conn, was_idle = pool.obtain(deadline, fresh=not first)
                if first:
                    reused = was_idle
                    if was_idle:
                        _count(host, "reused")
                    waited = int((time.time() - t0) * 1000)
                    if waited >= 5:
                        _count(host, "waited_ms", waited)
            left = deadline - time.time()
            if left <= 0.05:
                raise SourceError("timed out before the request could be sent")
            try:
                conn.sock.settimeout(max(0.3, left))
                conn.request("POST" if data is not None else "GET", target, body=data, headers=headers)
                resp = conn.getresponse()
                body = resp.read()
            except Exception as e:
                _close(conn)
                conn = None
                _forget_tls_error()
                msg = str(e) or e.__class__.__name__
                timed_out = isinstance(e, TimeoutError) or "timed out" in msg
                if reused and first and data is None and not timed_out and isinstance(e, (http.client.HTTPException, OSError)):
                    # the far end closed a kept connection; nothing was answered, so ask again on a new one
                    _count(host, "stale_retried")
                    first = False
                    continue
                raise SourceError("The read operation timed out" if timed_out else msg[:160]) from None
            keep = not resp.will_close
            if 300 <= resp.status < 400:
                raise _Redirected()
            if resp.status >= 400:
                raise SourceError("HTTP %d %s" % (resp.status, body[:200].decode(errors="replace")))
            return body
    finally:
        pool.give_back(conn, keep)


def q(url, params):
    return url + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


# Tests set this to a function(url, params, data) -> parsed JSON to run fully offline.
TRANSPORT = None


def get_json(url, params=None, timeout=5.0, data=None, headers=None):
    """GET (or POST when data is given) and parse JSON. Raises SourceError on any failure."""
    if TRANSPORT is not None:
        t0 = time.time()
        j = TRANSPORT(url, dict(params or {}), data)
        if isinstance(j, Exception):
            raise SourceError(str(j))
        if isinstance(j, dict) and j.get("error"):
            raise SourceError("service error: %s" % str(j["error"])[:160])
        return j, int((time.time() - t0) * 1000)
    if params:
        url = q(url, params)
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    t0 = time.time()
    deadline = t0 + max(0.5, timeout)
    body = None
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.netloc in POOL_HOSTS and not _proxied(parts.hostname):
        try:
            body = _pooled(parts.netloc, parts.path + ("?" + parts.query if parts.query else ""), h, data, deadline)
        except _Redirected:
            body = None
    req = urllib.request.Request(url, data=data, headers=h)
    tries = 0
    while body is None:
        left = deadline - time.time()
        tries += 1
        last = tries >= MAX_TRIES or left <= 2 * CONNECT_S      # the last try may use all the time that is left
        _TL.connect_s = max(0.3, left if last else CONNECT_S)
        try:
            with _OPENER.open(req, timeout=max(0.5, left)) as r:
                body = r.read()
            break
        except _ConnectFailed as e:
            _forget_tls_error()
            if last or deadline - time.time() < 0.6:
                raise SourceError("connection failed after %d tr%s: %s" % (tries, "y" if tries == 1 else "ies", str(e)[:110])) from None
            time.sleep(0.15)
        except urllib.error.HTTPError as e:
            try:
                detail = e.read()[:200].decode(errors="replace")
            except Exception:
                detail = ""
            raise SourceError("HTTP %d %s" % (e.code, detail)) from None
        except Exception as e:
            _forget_tls_error()
            raise SourceError(str(e)[:160]) from None
    try:
        j = json.loads(body.decode(errors="replace"))
    except Exception:
        raise SourceError("not json: %r" % body[:80]) from None
    if isinstance(j, dict) and j.get("error"):
        raise SourceError("service error: %s" % str(j["error"])[:160])
    j_ms = int((time.time() - t0) * 1000)
    return j, j_ms


def get_text(url, timeout=5.0, headers=None):
    h = {"User-Agent": UA, "Accept": "text/html,*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=max(0.5, timeout)) as r:
            return r.read().decode(errors="replace")
    except Exception as e:
        raise SourceError(str(e)[:160]) from None


# ---------------------------------------------------------------- ArcGIS REST
def arcgis_query(layer_url, lat, lon, timeout=5.0, distance_m=None, out_fields="*", where="1=1",
                 count_only=False, max_records=10, order_by=None):
    params = {
        "geometry": "%s,%s" % (lon, lat),
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "false",
        "f": "json",
    }
    if distance_m:
        params["distance"] = str(distance_m)
        params["units"] = "esriSRUnit_Meter"
    if count_only:
        params["returnCountOnly"] = "true"
    else:
        params["resultRecordCount"] = str(max_records)
        if order_by:
            params["orderByFields"] = order_by
    j, ms = get_json(layer_url + "/query", params, timeout=timeout)
    if count_only:
        return int(j.get("count", 0)), ms
    feats = j.get("features") or []
    return [f.get("attributes", {}) for f in feats], ms


def arcgis_layers(service_url, timeout=5.0):
    """[(id, name)] for a MapServer / FeatureServer."""
    j, _ = get_json(service_url, {"f": "json"}, timeout=timeout)
    return [(l.get("id"), l.get("name") or "") for l in (j.get("layers") or [])]


# ---------------------------------------------------------------- Socrata SODA
def socrata(domain, dataset_id, params, timeout=5.0):
    url = "https://%s/resource/%s.json" % (domain, dataset_id)
    j, ms = get_json(url, params, timeout=timeout)
    if not isinstance(j, list):
        raise SourceError("unexpected shape")
    return j, ms


def meters(lat1, lon1, lat2, lon2):
    """Flat-earth distance in metres; fine inside one city."""
    import math
    try:
        return int(round(111320.0 * math.hypot(float(lat1) - float(lat2), (float(lon1) - float(lon2)) * math.cos(math.radians(float(lat1))))))
    except Exception:
        return None


def pick(d, *patterns, default=None):
    """First value in dict d whose key matches any regex pattern (case-insensitive)."""
    import re
    for p in patterns:
        rx = re.compile(p, re.I)
        for k, v in d.items():
            if rx.search(k) and v not in (None, "", " "):
                return v
    return default
