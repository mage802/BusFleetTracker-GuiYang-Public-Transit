"""Read-only client for the verified official H5 endpoint."""
import http.client
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://h5.mygolbs.com/ApiData.do"
READ_COMMANDS = {"101", "102", "103", "104", "106", "110", "114", "115", "116", "119", "207"}


class ApiError(Exception):
    """A rejected or malformed API response; never interpreted as no buses."""


class AccessDenied(ApiError):
    """Access rejection or rate limiting: stop, do not switch profiles."""


def is_transient(error):
    if isinstance(error, (AccessDenied, ApiError)):
        return False
    if isinstance(error, urllib.error.HTTPError):
        return error.code in (408, 500, 502, 503, 504)
    return isinstance(error, (urllib.error.URLError, TimeoutError, socket.timeout,
                              OSError, http.client.IncompleteRead, http.client.RemoteDisconnected))


class Client:
    def __init__(self, city_name="贵阳市", city_code="520100", timeout=25):
        self.city_name = city_name
        self.city_code = city_code
        self.timeout = timeout

    def call(self, cmd, **params):
        cmd = str(cmd)
        if cmd not in READ_COMMANDS:
            raise ValueError("Unsupported read-only CMD: " + cmd)
        form = {"CITYNAME": self.city_name}
        if self.city_code:
            form["CITYCODE"] = self.city_code
        form.update(params)
        form["CMD"] = cmd
        request = urllib.request.Request(
            API_URL, data=urllib.parse.urlencode(form).encode("utf-8"),
            headers={"Origin": "https://h5.mygolbs.com",
                     "Referer": "https://h5.mygolbs.com/",
                     "User-Agent": "Mozilla/5.0",
                     "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 429):
                raise AccessDenied(f"HTTP {exc.code}: access rejected; stop collection") from exc
            raise
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ApiError(f"Non-JSON response ({len(raw)} bytes); check Origin and endpoint") from exc
        if not isinstance(obj, dict) or str(obj.get("status")) != "1":
            raise ApiError("Business failure: " + str(obj)[:400])
        return obj


def classify_realtime(obj):
    """Return outcome and unique raw identifier strings, preserving leading zeros."""
    buses = obj.get("list")
    if not isinstance(buses, list):
        if str(obj.get("hasReal")) == "0":
            return "unavailable", []
        raise ApiError("Missing vehicle list; cannot interpret as an empty result")
    ids = set()
    for bus in buses:
        if not isinstance(bus, dict):
            raise ApiError("Malformed vehicle entry")
        value = bus.get("busNumber")
        if value is None or not str(value).strip():
            raise ApiError("Vehicle entry missing busNumber")
        ids.add(str(value).strip())
    return "success", sorted(ids)
