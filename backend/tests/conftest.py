"""Configuração comum dos testes: cada teste usa um banco novo, numa pasta temporária."""

import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import criar_app  # noqa: E402


@pytest.fixture
def app(tmp_path):
    return criar_app(str(tmp_path / "teste.db"))


@pytest.fixture
def cliente(app):
    return TestClient(app)


@pytest.fixture
def servico(app):
    return app.state.servico


@pytest.fixture
def banco(servico):
    """Conexão direta com o SQLite, para conferir o que foi gravado."""
    return servico._con


def venda(id_transacao, uid="532F07FE", itens=(("1", 1),), terminal="POS-01", **extras):
    pacote = {
        "id_transacao": id_transacao,
        "terminal": terminal,
        "uid_cartao": uid,
        "itens": [{"codigo": codigo, "quantidade": quantidade} for codigo, quantidade in itens],
        "criada_em": "2026-11-12T14:00:00Z",
        "relogio_sincronizado": True,
        "origem": "online",
    }
    pacote.update(extras)
    return pacote
