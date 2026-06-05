#!/usr/bin/env python3
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# server.py
# Serves the premium dashboard and streams processed paper assets on Port 8585.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import os
import sys
import socket
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = 8585
PROCESSED_PATH = "/mnt/raid0/monolithic_pdf_folderv3/illoinois_edu/_processed"
if len(sys.argv) > 1:
    PROCESSED_PATH = sys.argv[1]
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))

class DashboardHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        # Strip query parameters and hash fragments
        path = path.split('?', 1)[0]
        path = path.split('#', 1)[0]
        
        # Intercept and map /_processed/ assets to the external processed SSD folder
        if path.startswith("/_processed/"):
            rel_path = path[len("/_processed/"):]
            rel_path = rel_path.lstrip("/")
            return os.path.join(PROCESSED_PATH, rel_path)
            
        # Intercept /api/sync to trigger Neo4j import
        if path == "/api/sync":
            import subprocess
            try:
                subprocess.run([sys.executable, "neo4j_importer.py"], cwd=DASHBOARD_DIR)
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f'{{"error":"{e}"}}'.encode())
            return None
            
        # Default behavior: serve from dashboard directory
        return os.path.join(DASHBOARD_DIR, path.lstrip("/"))

    def do_GET(self):
        # Override do_GET to handle our API which returns None from translate_path
        path = self.translate_path(self.path)
        if path is None:
            return
        super().do_GET()

def get_lan_ip():
    """Helper to query the local hostname to get the actual LAN IP."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't need to be reachable, just triggers OS interface lookup
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = 'localhost'
    finally:
        s.close()
    return ip

def run():
    os.chdir(DASHBOARD_DIR)
    lan_ip = get_lan_ip()
    
    server_address = ('0.0.0.0', PORT)
    httpd = ThreadingHTTPServer(server_address, DashboardHandler)
    
    print(f"\n🦞 LOBSTER DASHBOARD SERVER")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"🟢 Local: http://localhost:{PORT}")
    print(f"🟢 LAN:   http://{lan_ip}:{PORT}")
    print(f"📂 Assets: {PROCESSED_PATH}")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("Press Ctrl+C to terminate the web server.\n")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Stopping dashboard web server...")
        sys.exit(0)

if __name__ == '__main__':
    run()
