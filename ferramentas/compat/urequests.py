"""
urequests.py (versão para o COMPUTADOR) - usada só pelo simulador

No ESP32, o urequests vem com o MicroPython. No computador ele não existe,
então este arquivo imita a mesma interface usando o urllib do Python, para
que o código da pasta esp32/ rode no simulador sem nenhuma alteração.

NÃO envie este arquivo para a placa.
"""

import json as _json
import socket
import urllib.error
import urllib.request


class Resposta:
    def __init__(self, status_code, conteudo):
        self.status_code = status_code
        self.content = conteudo

    @property
    def text(self):
        return self.content.decode("utf-8")

    def json(self):
        return _json.loads(self.content.decode("utf-8"))

    def close(self):
        pass


def request(method, url, data=None, json=None, headers=None, timeout=None):
    if not url.startswith(("http://", "https://")):
        raise ValueError("Unsupported protocol: " + url.split(":")[0])
    if json is not None:
        data = _json.dumps(json).encode("utf-8")
    elif isinstance(data, str):
        data = data.encode("utf-8")
    pedido = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(pedido, timeout=timeout or 10) as resposta:
            return Resposta(resposta.status, resposta.read())
    except urllib.error.HTTPError as erro:
        # Como o urequests: um 4xx/5xx é uma resposta, não uma exceção
        return Resposta(erro.code, erro.read())
    except urllib.error.URLError as erro:
        raise OSError(str(erro.reason))
    except socket.timeout:
        raise OSError("ETIMEDOUT")


def get(url, **parametros):
    return request("GET", url, **parametros)


def post(url, **parametros):
    return request("POST", url, **parametros)
