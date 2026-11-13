"""
banco.py - Conexão com o SQLite e transações atômicas

O arquivo do banco fica em database/bytebank.db (fora do GitHub: está no
.gitignore). Na primeira vez, ele é criado a partir de database/schema.sql
e preenchido com database/seed.sql.

ATOMICIDADE

Uma venda mexe em várias tabelas: baixa o estoque de cada produto, debita o
saldo da conta, grava a transação, os itens e a auditoria. Ou tudo isso
acontece, ou nada acontece. Para isso, todas as alterações rodam dentro de

    with transacao(conexao):
        ...vários UPDATE e INSERT...

que faz BEGIN IMMEDIATE no começo, COMMIT se o bloco terminar bem e
ROLLBACK se qualquer erro acontecer no meio (inclusive um CHECK do banco,
como saldo negativo). Se a luz cair no meio da venda, o SQLite também
desfaz a parte que não chegou ao COMMIT.

BEGIN IMMEDIATE reserva a escrita no banco já no começo: duas vendas ao
mesmo tempo não conseguem ler o mesmo saldo e as duas debitarem.
"""

import os
import sqlite3
from contextlib import contextmanager

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASTA_DATABASE = os.path.join(RAIZ, "database")
CAMINHO_PADRAO = os.path.join(PASTA_DATABASE, "bytebank.db")
ARQUIVO_SCHEMA = os.path.join(PASTA_DATABASE, "schema.sql")
ARQUIVO_SEED = os.path.join(PASTA_DATABASE, "seed.sql")


def conectar(caminho=CAMINHO_PADRAO):
    """Abre o banco (criando-o na primeira vez) e devolve a conexão."""
    novo = caminho == ":memory:" or not os.path.exists(caminho)
    # isolation_level=None: nós mesmos controlamos BEGIN/COMMIT (ver transacao)
    conexao = sqlite3.connect(caminho, isolation_level=None, check_same_thread=False)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    conexao.execute("PRAGMA busy_timeout = 5000")
    if novo:
        criar_tabelas(conexao)
    return conexao


def criar_tabelas(conexao, com_dados_de_teste=True):
    with open(ARQUIVO_SCHEMA, encoding="utf-8") as arquivo:
        conexao.executescript(arquivo.read())
    if com_dados_de_teste:
        with open(ARQUIVO_SEED, encoding="utf-8") as arquivo:
            conexao.executescript(arquivo.read())


def recriar(caminho=CAMINHO_PADRAO):
    """Apaga o banco e cria de novo, com os dados de teste."""
    if caminho != ":memory:" and os.path.exists(caminho):
        os.remove(caminho)
    return conectar(caminho)


@contextmanager
def transacao(conexao):
    """Executa o bloco como UMA transação: tudo ou nada."""
    conexao.execute("BEGIN IMMEDIATE")
    try:
        yield conexao
    except BaseException:
        conexao.execute("ROLLBACK")
        raise
    else:
        conexao.execute("COMMIT")
