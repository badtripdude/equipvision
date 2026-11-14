"""Start the local server.

    .venv/Scripts/python run.py            HTTPS on 0.0.0.0:8443 — open from the phone
    .venv/Scripts/python run.py --no-tls   plain HTTP on 127.0.0.1:8000 — desktop development only
"""
import argparse
import os
import sys

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-tls", action="store_true", help="plain HTTP, localhost only")
    parser.add_argument("--port", type=int)
    parser.add_argument("--db", help="path of the database file (default: server/data/equipvision.sqlite3)")
    args = parser.parse_args()
    if args.db:
        os.environ["EQUIPVISION_DB"] = os.path.abspath(args.db)
    if args.no_tls:
        os.environ["EQUIPVISION_DEV"] = "1"      # enables developer-only helpers
    from app.main import app
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.no_tls:
        port = args.port or 8000
        print(f"Только для разработки: http://localhost:{port}", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=port)
        return

    from app.certs import ensure_cert

    cert, key, ips = ensure_cert()
    port = args.port or 8443
    print("Откройте на телефоне (та же сеть Wi-Fi):", flush=True)
    for ip in ips:
        if ip != "127.0.0.1" and not ip.startswith("169.254."):  # skip loopback and link-local
            print(f"    https://{ip}:{port}", flush=True)
    print("Браузер предупредит о самоподписанном сертификате — нужно согласиться один раз.", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, ssl_certfile=cert, ssl_keyfile=key)


if __name__ == "__main__":
    main()
