#!/usr/bin/env python3
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# cosmos_server.py
# Serves the CosmosGL (@cosmos.gl/graph) dashboard on Port 8686.
# Companion to server.py (port 8585) — same Neo4j graph, different renderer.
# Neo4j credentials stay server-side; the browser only ever talks to this
# process over plain HTTP/JSON.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import json
import os
import re
import socket
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import neo4j

PORT = 8686
NEO4J_URL = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "password123"
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))

NODE_QUERY = "MATCH (n) RETURN id(n) AS id, labels(n)[0] AS type, coalesce(n.name, n.title, toString(id(n))) AS label, n.fx AS fx, n.fy AS fy"
EDGE_QUERY = "MATCH (s)-[r]->(t) RETURN id(s) AS source, id(t) AS target, type(r) AS type"
NODE_DETAIL_QUERY = "MATCH (n) WHERE id(n) = $id RETURN labels(n)[0] AS type, properties(n) AS props"
PDF_PATH_QUERY = "MATCH (p:Paper) WHERE id(p) = $id RETURN p.pdf_path AS pdf_path"

NODE_ID_RE = re.compile(r"^/(?:api/node|pdf)/(\d+)$")


def run_query(query, **params):
    driver = neo4j.GraphDatabase.driver(NEO4J_URL, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            return [dict(r) for r in session.run(query, **params)]
    finally:
        driver.close()


def fetch_graph():
    nodes = run_query(NODE_QUERY)
    edges = run_query(EDGE_QUERY)

    # Nodes with no fx/fy (isolated, no edges) fall back to origin so the
    # frontend can still position every point deterministically.
    for n in nodes:
        if n["fx"] is None:
            n["fx"] = 0.0
        if n["fy"] is None:
            n["fy"] = 0.0

    return {"nodes": nodes, "edges": edges}


def fetch_node_detail(node_id):
    rows = run_query(NODE_DETAIL_QUERY, id=node_id)
    if not rows:
        return None
    return rows[0]


def fetch_pdf_path(node_id):
    rows = run_query(PDF_PATH_QUERY, id=node_id)
    if not rows:
        return None
    return rows[0].get("pdf_path")


class CosmosHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path == "/":
            path = "/cosmos_dashboard.html"
        return os.path.join(DASHBOARD_DIR, path.lstrip("/"))

    def _send_json(self, status, obj):
        payload = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path == "/api/graph":
            try:
                self._send_json(200, fetch_graph())
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        node_match = NODE_ID_RE.match(path)
        if node_match and path.startswith("/api/node/"):
            node_id = int(node_match.group(1))
            try:
                detail = fetch_node_detail(node_id)
                if detail is None:
                    self._send_json(404, {"error": "node not found"})
                else:
                    self._send_json(200, detail)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        if node_match and path.startswith("/pdf/"):
            node_id = int(node_match.group(1))
            self._serve_pdf(node_id)
            return

        super().do_GET()

    def _serve_pdf(self, node_id):
        try:
            pdf_path = fetch_pdf_path(node_id)
        except Exception as e:
            self._send_json(500, {"error": str(e)})
            return

        if not pdf_path or not pdf_path.lower().endswith(".pdf") or not os.path.isfile(pdf_path):
            message = (
                f"Source PDF is not available on disk.\n\nStored path: {pdf_path or '(none)'}\n\n"
                "It may have been moved, archived, or cleaned up after processing."
            ).encode()
            self.send_response(404)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)
            return

        size = os.path.getsize(pdf_path)
        self.send_response(200)
        self.send_header("Content-type", "application/pdf")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'inline; filename="{os.path.basename(pdf_path)}"')
        self.end_headers()
        with open(pdf_path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "localhost"
    finally:
        s.close()
    return ip


def run():
    os.chdir(DASHBOARD_DIR)
    lan_ip = get_lan_ip()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), CosmosHandler)

    print("\n\U0001f30c COSMOSGL DASHBOARD SERVER")
    print("━" * 64)
    print(f"\U0001f7e2 Local: http://localhost:{PORT}")
    print(f"\U0001f7e2 LAN:   http://{lan_ip}:{PORT}")
    print(f"\U0001f5c3️  Neo4j: {NEO4J_URL}")
    print("━" * 64)
    print("Press Ctrl+C to terminate the web server.\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\U0001f6d1 Stopping CosmosGL dashboard server...")
        sys.exit(0)


if __name__ == "__main__":
    run()
