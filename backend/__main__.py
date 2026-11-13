"""
Inicia a API do ByteBank Pay.  Da pasta raiz do repositório:

    python -m backend                 sobe na porta 8000
    python -m backend --recriar       apaga o banco e recria com os dados de teste
    python -m backend --porta 8080    outra porta
"""

import argparse
import logging
import socket

import uvicorn

from . import banco
from .app import criar_app


def ip_local():
    """IP deste computador na rede local: é ele que vai no API_URL do ESP32."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))   # não envia nada; só descobre a interface de saída
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    parser = argparse.ArgumentParser(description="API do ByteBank Pay")
    parser.add_argument("--porta", type=int, default=8000)
    parser.add_argument("--banco", default=banco.CAMINHO_PADRAO, help="arquivo do SQLite")
    parser.add_argument("--recriar", action="store_true", help="apaga o banco e recria com os dados de teste")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
    if args.recriar:
        banco.recriar(args.banco).close()
        print("Banco recriado com os dados de teste: {}".format(args.banco))

    app = criar_app(args.banco)
    print("=" * 62)
    print(" ByteBank Pay - API (Entrega 3)")
    print("=" * 62)
    print(" No config.py do ESP32, use:")
    print('     API_URL = "http://{}:{}"'.format(ip_local(), args.porta))
    print(" Testar pelo navegador:  http://localhost:{}/docs".format(args.porta))
    print(" Banco de dados:         {}".format(args.banco))
    print(" O ESP32 e este computador precisam estar na MESMA rede.")
    print(" Para encerrar: Ctrl+C")
    print("=" * 62, flush=True)
    uvicorn.run(app, host="0.0.0.0", port=args.porta, log_level="warning")


if __name__ == "__main__":
    main()
