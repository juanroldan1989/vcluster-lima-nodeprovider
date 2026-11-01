# SPDX-License-Identifier: Apache-2.0
"""Exercise the real HTTP handler using in-memory socket streams."""
import base64
import io
import json
from types import SimpleNamespace
import unittest

from helper import server
from tests.test_helper import FakeLima, request
import tempfile


class Socket:
    def __init__(self, data):
        self.input = io.BytesIO(data)
        self.output = io.BytesIO()

    def makefile(self, *_args):
        return self.input

    def sendall(self, data):
        self.output.write(data)


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = FakeLima(self.temp.name)
        self.token = "synthetic-test-password" * 2
        self.handler = server.make_handler(self.manager, self.token)

    def call(self, method, path, data=None, password=None, auth=True, length=None, raw=None):
        body = raw if raw is not None else json.dumps(data).encode() if data is not None else b""
        headers = [f"{method} {path} HTTP/1.0", "Host: localhost"]
        if auth:
            value = "platform:" + (self.token if password is None else password)
            headers.append("Authorization: Basic " + base64.b64encode(value.encode()).decode())
        headers.append("Content-Length: " + str(len(body) if length is None else length))
        sock = Socket("\r\n".join(headers).encode() + b"\r\n\r\n" + body)
        self.handler(sock, ("127.0.0.1", 12345), SimpleNamespace(server_name="localhost", server_port=18443))
        response_headers, response_body = sock.output.getvalue().split(b"\r\n\r\n", 1)
        return int(response_headers.split()[1]), json.loads(response_body)

    def test_authentication_precedes_mutation(self):
        for options in ({"auth": False}, {"password": "wrong"}, {"password": "non-ascii-\u00e9"}):
            with self.subTest(options=options):
                self.assertEqual(self.call("POST", "/nodes", request(), **options)[0], 401)
        self.assertEqual(self.manager.started, 0)
        self.assertEqual(list(self.manager.state.glob("*.json")), [])

    def test_health_is_authenticated_and_reports_capacity(self):
        self.assertEqual(self.call("GET", "/healthz", auth=False)[0], 401)
        status, body = self.call("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["max_nodes"], 8)
        self.assertEqual(body["worker_size"], {"cpus": 1, "memory_gib": 2, "disk_gib": 30})
        self.assertEqual(body["config_id"], server.configuration_id(self.manager.config))

    def test_create_read_repair_and_idempotent_delete(self):
        data = request()
        path = "/nodes/" + data["id"]
        self.assertEqual(self.call("POST", "/nodes", data)[0], 200)
        self.assertTrue(self.call("GET", path)[1]["ready"])
        self.manager.vms.clear()
        self.assertFalse(self.call("GET", path)[1]["ready"])
        self.assertTrue(self.call("PUT", path, data)[1]["ready"])
        self.assertEqual(self.call("DELETE", path), (200, {"id": data["id"]}))
        self.assertEqual(self.call("DELETE", path)[0], 200)
        self.assertEqual(self.call("GET", path)[0], 404)

    def test_malformed_bodies_and_lengths_do_not_create_workers(self):
        cases = ({"raw": b"{"}, {"data": []}, {"data": None}, {"data": request(), "length": "bad"},
                 {"data": request(), "length": server.MAX_BODY + 1})
        for options in cases:
            with self.subTest(options=options):
                self.assertIn(self.call("POST", "/nodes", **options)[0], (400, 413))
        self.assertEqual(self.manager.started, 0)

    def test_put_identity_must_match_url(self):
        self.assertEqual(self.call("PUT", "/nodes/" + "b" * 24, request())[0], 400)
        self.assertEqual(self.manager.started, 0)

    def test_unknown_path_and_method(self):
        self.assertEqual(self.call("GET", "/nodes/../escape")[0], 404)
        self.assertEqual(self.call("POST", "/nodes/" + "a" * 24, request())[0], 405)
        self.assertEqual(self.call("PATCH", "/nodes/" + "a" * 24, request())[0], 405)

    def test_bootstrap_failure_returns_generic_error(self):
        self.manager.fail_bootstrap = True
        status, body = self.call("POST", "/nodes", request())
        self.assertEqual(status, 502)
        self.assertNotIn("user_data", body)
        self.assertFalse(self.manager.load("a" * 24)["ready"])
