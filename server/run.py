"""Start the local server.

    .venv/Scripts/python run.py            HTTPS on 0.0.0.0:8443 — open from a phone in the same network
    .venv/Scripts/python run.py --public   plain HTTP on 0.0.0.0:8080 — for users outside the network,
                                           behind an HTTPS tunnel or reverse proxy (demonstration)
    .venv/Scripts/python run.py --no-tls   plain HTTP on 127.0.0.1:8000 — desktop development only
"""
import argparse
import os
import sys

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-tls", action="store_true", help="plain HTTP, localhost only, developer helpers on")
    mode.add_argument("--public", action="store_true",
                      help="plain HTTP on all interfaces, to be put behind an HTTPS tunnel or reverse proxy")
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

    if args.public:
        # The tunnel or proxy in front terminates HTTPS: the browser gives the camera and the microphone
        # only to an https:// page. Its X-Forwarded-* headers tell the server the real scheme and client.
        port = args.port or 8080
        print("Режим демонстрации для внешних пользователей.", flush=True)
        print(f"Сервер слушает http://0.0.0.0:{port} и ждёт подключений через HTTPS-туннель или обратный прокси,", flush=True)
        print(f"например: cloudflared tunnel --url http://localhost:{port}", flush=True)
        print("Откройте пользователям адрес https://…, который выдаст туннель: без HTTPS браузер не даст камеру.", flush=True)
        print("Внимание: пароли демонстрационных учётных записей лежат в репозитории — не оставляйте сервер "
              "открытым дольше показа.", flush=True)
        uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
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
