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


def fetch_graph():
    driver = neo4j.GraphDatabase.driver(NEO4J_URL, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            nodes = [dict(r) for r in session.run(NODE_QUERY)]
            edges = [dict(r) for r in session.run(EDGE_QUERY)]
    finally:
        driver.close()

    # Nodes with no fx/fy (isolated, no edges) fall back to origin so the
    # frontend can still position every point deterministically.
    for n in nodes:
        if n["fx"] is None:
            n["fx"] = 0.0
        if n["fy"] is None:
            n["fy"] = 0.0

    return {"nodes": nodes, "edges": edges}


class CosmosHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path == "/":
            path = "/cosmos_dashboard.html"
        return os.path.join(DASHBOARD_DIR, path.lstrip("/"))

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/api/graph":
            try:
                payload = json.dumps(fetch_graph()).encode()
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
            return
        super().do_GET()


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
