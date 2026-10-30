"""
arquivos.py - Gravação segura de JSON na memória flash (MicroPython)

Se a placa desligar no meio de uma gravação, o arquivo pode ficar pela
metade. Para evitar isso, os dados são escritos primeiro num arquivo
temporário e só depois ele substitui o original. Assim, ou fica a versão
antiga inteira, ou a nova inteira.
"""

import json
import os

AUSENTE = "ausente"
CORROMPIDO = "corrompido"
OK = "ok"


def substituir(origem, destino):
    """Renomeia origem para destino, mesmo que destino já exista.

    Em alguns sistemas de arquivos do MicroPython, renomear por cima de um
    arquivo existente falha; nesse caso o antigo é removido antes.
    """
    try:
        os.rename(origem, destino)
    except OSError:
        try:
            os.remove(destino)
        except OSError:
            pass
        os.rename(origem, destino)


def gravar_json(caminho, dados):
    """Grava `dados` em JSON de forma segura. Devolve True se conseguiu."""
    temporario = caminho + ".tmp"
    try:
        with open(temporario, "w") as arquivo:
            arquivo.write(json.dumps(dados))
        substituir(temporario, caminho)
        return True
    except OSError as erro:
        print("[ARQUIVO] Falha ao gravar {}: {}".format(caminho, erro))
        return False


def ler_json(caminho):
    """Lê um arquivo JSON. Devolve (dados, situacao).

    situacao é OK, AUSENTE (o arquivo não existe, o que é normal no primeiro
    uso) ou CORROMPIDO (existe, mas o conteúdo não é um JSON válido).
    """
    try:
        with open(caminho) as arquivo:
            conteudo = arquivo.read()
    except OSError:
        return None, AUSENTE
    try:
        return json.loads(conteudo), OK
    except ValueError:
        return None, CORROMPIDO


def guardar_corrompido(caminho):
    """Renomeia um arquivo corrompido em vez de apagá-lo, para que o
    conteúdo possa ser examinado depois. Devolve o novo nome."""
    destino = caminho + ".corrompido"
    try:
        substituir(caminho, destino)
        return destino
    except OSError:
        return None
