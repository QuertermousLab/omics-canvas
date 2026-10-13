#!/usr/bin/env python3
"""A small stand-in for gateway/apache.conf (standard library only), for CI and local tests:
/genome-canvas/... -> Genome Canvas (prefix stripped, X-Forwarded-Prefix set), / -> /hub/, the rest -> the hub.

  python3 gateway/dev_gateway.py --port 8892 --hub http://127.0.0.1:8893 --canvas http://127.0.0.1:8000
"""
import argparse
import http.client
import http.server
import urllib.parse

HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding', 'upgrade'}


def make_handler(hub, canvas):
    class Gateway(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def forward(self):
            if self.path in ('/', ''):
                self.send_response(302)
                self.send_header('Location', '/hub/')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            target, path, extra = hub, self.path, {}
            if self.path == '/genome-canvas' or self.path.startswith('/genome-canvas/'):
                target, path = canvas, self.path[len('/genome-canvas'):] or '/'
                extra = {'X-Forwarded-Prefix': '/genome-canvas'}
            length = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP and k.lower() != 'host'}
            headers.update(extra, Host=target.netloc)
            conn = http.client.HTTPConnection(target.hostname, target.port, timeout=600)
            try:
                conn.request(self.command, path, body=body, headers=headers)
                res = conn.getresponse()
                data = res.read()
            except OSError as e:
                self.send_error(502, f'upstream unavailable: {e}')
                return
            finally:
                conn.close()
            self.send_response(res.status, res.reason)
            for k, v in res.getheaders():
                if k.lower() not in HOP and k.lower() != 'content-length':
                    self.send_header(k, v)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = forward

        def log_message(self, fmt, *args):
            pass

    return Gateway


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8892)
    ap.add_argument('--hub', default='http://127.0.0.1:8893')
    ap.add_argument('--canvas', default='http://127.0.0.1:8000')
    a = ap.parse_args()
    handler = make_handler(urllib.parse.urlsplit(a.hub), urllib.parse.urlsplit(a.canvas))
    http.server.ThreadingHTTPServer((a.host, a.port), handler).serve_forever()


if __name__ == '__main__':
    main()
