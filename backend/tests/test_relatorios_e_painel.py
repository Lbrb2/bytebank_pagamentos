"""Relatórios do backend (Entrega 4) e cálculos do painel (dashboard/dados.py)."""

import os
import sys
from datetime import datetime, timedelta

from backend.tests.conftest import RAIZ, venda

sys.path.insert(0, os.path.join(RAIZ, "dashboard"))

import dados  # noqa: E402


# ---------------------------------------------------------------- backend

def test_mais_vendidos_top_k_com_heap(cliente):
    cliente.post("/transacoes", json=venda("V1", itens=(("2", 3), ("1", 1))))
    cliente.post("/transacoes", json=venda("V2", itens=(("1", 2), ("6", 1))))
    cliente.post("/transacoes", json=venda("V3", itens=(("4", 3),)))
    cliente.post("/estornos", json={"id_transacao": "V3", "terminal": "POS-01"})     # estorno não conta
    top = cliente.get("/relatorios/mais-vendidos?k=2").json()["produtos"]
    # Café e Água empatam em 3 unidades: desempata pela receita (Café R$ 15 > Água R$ 9)
    assert [(p["nome"], p["unidades"], p["receita_centavos"]) for p in top] == [("Café", 3, 1500), ("Água", 3, 900)]


def test_filtros_de_periodo_e_terminal(cliente, banco):
    cliente.post("/transacoes", json=venda("V1"))
    cliente.post("/transacoes", json=venda("V2", terminal="POS-02"))
    banco.execute("UPDATE transacoes SET recebida_em = '2026-01-01T12:00:00Z' WHERE id_transacao = 'V1'")
    assert [t["id_transacao"] for t in cliente.get("/transacoes?terminal=POS-02").json()["transacoes"]] == ["V2"]
    recentes = cliente.get("/transacoes?desde=2026-06-01T00:00:00Z").json()["transacoes"]
    assert [t["id_transacao"] for t in recentes] == ["V2"]
    assert cliente.get("/relatorios/mais-vendidos?terminal=POS-09").json()["produtos"] == []


def test_lista_de_comandos(cliente):
    cliente.post("/terminais/POS-01/comandos", json={"tipo": "mensagem", "texto": "Oi"})
    cliente.post("/terminais/POS-01/comandos/1/confirmacao", json={"resultado": "ok"})
    cliente.post("/terminais/POS-01/comandos", json={"tipo": "bloquear"})
    comandos = cliente.get("/comandos").json()["comandos"]
    assert [(c["tipo"], c["status"]) for c in comandos] == [("bloquear", "pendente"), ("mensagem", "executado")]
    assert comandos[1]["parametros"] == {"texto": "Oi", "segundos": 10}


# ---------------------------------------------------------------- painel

def transacao(status, centavos, hora, origem="online", motivo=None):
    momento = datetime(2026, 11, 19, hora, 30, tzinfo=dados.FUSO_LOCAL)
    return {"status": status, "total_centavos": centavos, "origem": origem, "motivo": motivo,
            "recebida_em": dados.para_utc_iso(momento)}


def test_indicadores():
    lista = [transacao("aprovada", 1300, 9), transacao("aprovada", 700, 10, "contingencia"),
             transacao("recusada", 0, 10, motivo="Saldo insuficiente"), transacao("estornada", 500, 11)]
    numeros = dados.indicadores(lista)
    assert numeros["faturamento_centavos"] == 2000 and numeros["vendas"] == 2
    assert numeros["ticket_medio_centavos"] == 1000 and numeros["offline"] == 1
    assert numeros["recusadas"] == 1 and numeros["taxa_recusa"] == 0.25
    assert numeros["estornos"] == 1 and numeros["estornado_centavos"] == 500
    assert dados.indicadores([])["ticket_medio_centavos"] == 0


def test_faturamento_por_hora_inclui_horas_sem_venda():
    agora = datetime(2026, 11, 19, 12, 10, tzinfo=dados.FUSO_LOCAL)
    lista = [transacao("aprovada", 1300, 9), transacao("aprovada", 700, 9), transacao("recusada", 0, 11)]
    linhas, granularidade = dados.faturamento_no_tempo(lista, "Hoje", agora)
    assert granularidade == "hora" and len(linhas) == 13            # 00h até 12h
    por_hora = {l["rotulo"]: l["faturamento_centavos"] for l in linhas}
    assert por_hora["09h"] == 2000 and por_hora["11h"] == 0 and por_hora["00h"] == 0
    linhas, granularidade = dados.faturamento_no_tempo(lista, "Últimos 7 dias", agora)
    assert granularidade == "dia" and len(linhas) == 7 and linhas[-1]["faturamento_centavos"] == 2000


def test_situacao_do_terminal():
    agora = datetime(2026, 11, 19, 12, 0, tzinfo=dados.FUSO_LOCAL)
    contato = lambda segundos: dados.para_utc_iso(agora - timedelta(seconds=segundos))  # noqa: E731
    assert dados.situacao_do_terminal({"status": "ativo", "ultimo_contato": contato(20)}, agora) == ("bom", "Online")
    assert dados.situacao_do_terminal({"status": "ativo", "ultimo_contato": contato(600)}, agora) == (
        "atencao", "Sem contato há 10 min")
    assert dados.situacao_do_terminal({"status": "bloqueado", "ultimo_contato": None}, agora)[0] == "critico"
    assert dados.situacao_do_terminal({"status": "ativo", "ultimo_contato": None}, agora)[0] == "atencao"


def test_reais():
    assert dados.reais(125050) == "R$ 1.250,50"
    assert dados.reais(0) == "R$ 0,00"


# ---------------------------------------------------------------- extras do painel

def test_filtro_ate_e_historico_de_telemetria(cliente, banco):
    cliente.post("/transacoes", json=venda("V1"))
    banco.execute("UPDATE transacoes SET recebida_em = '2026-01-01T12:00:00Z'")
    cliente.post("/transacoes", json=venda("V2"))
    antigas = cliente.get("/transacoes?ate=2026-06-01T00:00:00Z").json()["transacoes"]
    assert [t["id_transacao"] for t in antigas] == ["V1"]
    assert "criada_no_terminal" in antigas[0] and "relogio_sincronizado" in antigas[0]
    for rssi in (-60, -65):
        cliente.post("/telemetria", json={"terminal": "POS-01", "rssi": rssi, "memoria_livre": 90000})
    historico = cliente.get("/telemetria?terminal=POS-01").json()["telemetria"]
    assert [h["rssi"] for h in historico] == [-60, -65]           # do mais antigo ao mais novo


def test_periodo_anterior_e_variacao():
    agora = datetime(2026, 11, 19, 10, 30, tzinfo=dados.FUSO_LOCAL)
    inicio, fim, rotulo = dados.periodo_anterior("Hoje", agora)
    assert (inicio.day, inicio.hour, fim.day, fim.hour, fim.minute) == (18, 0, 18, 10, 30)
    assert rotulo == "ontem até 10h30"
    assert dados.periodo_anterior("Tudo", agora) is None
    assert dados.variacao(1200, 1000) == ("▲ 20%", "bom")
    assert dados.variacao(800, 1000) == ("▼ 20%", "ruim")
    assert dados.variacao(7, 2, para_cima_e_bom=False, em_porcentagem=False) == ("▲ 5", "ruim")
    assert dados.variacao(5000, 200) == ("▲ 25x", "bom")
    assert dados.variacao(3, 3) == ("= igual", "neutro")
    assert dados.variacao(500, 0)[1] == "bom"


def test_previsao_de_estoque():
    produtos = [{"codigo": "1", "nome": "Café", "estoque": 40}, {"codigo": "2", "nome": "Água", "estoque": 9},
                {"codigo": "5", "nome": "Bolo", "estoque": 4}, {"codigo": "7", "nome": "Chocolate", "estoque": 0},
                {"codigo": "3", "nome": "Suco", "estoque": 25}]
    vendidos = [{"codigo": "1", "unidades": 10}, {"codigo": "2", "unidades": 18}]
    previsao = {p["nome"]: p for p in dados.previsao_de_estoque(produtos, vendidos)}
    assert previsao["Chocolate"]["nivel"] == "critico" and previsao["Chocolate"]["previsao"] == "Esgotado"
    assert previsao["Água"]["nivel"] == "critico" and previsao["Água"]["previsao"] == "Acaba em ~30 min"
    assert previsao["Café"]["nivel"] == "bom" and previsao["Café"]["previsao"] == "Dura ~4 h 00 min"
    assert previsao["Bolo"]["nivel"] == "atencao" and previsao["Bolo"]["horas"] is None
    assert previsao["Suco"]["previsao"] == "Sem vendas há 1 h"
    ordem = [p["nome"] for p in dados.previsao_de_estoque(produtos, vendidos)]
    assert ordem.index("Água") < ordem.index("Bolo") < ordem.index("Café")


def test_saude_dos_terminais():
    inicio = datetime(2026, 11, 19, 9, 0, tzinfo=dados.FUSO_LOCAL)
    registros = [{"terminal": "POS-01", "registrada_em": dados.para_utc_iso(inicio + timedelta(minutes=m)),
                  "rssi": -60 - (m % 2) * 2, "memoria_livre": (100 - m) * 1024, "fila_pendente": 0}
                 for m in range(30)]
    serie = dados.serie_de_telemetria(registros, "Hoje")
    assert len(serie) == 6                                         # 30 min em blocos de 5 min
    assert serie[0]["rssi"] == -60.8 and serie[0]["memoria_kb"] == 98.0
    assert dados.tendencia_de_memoria(serie).startswith("Memória livre caiu")
    estavel = [dict(l, memoria_kb=95.0) for l in serie]
    assert dados.tendencia_de_memoria(estavel) is None


def test_mapa_de_calor_e_fila_offline():
    def offline(espera_s, sincronizado=True):
        chegada = datetime(2026, 11, 19, 12, 0, tzinfo=dados.FUSO_LOCAL)
        return {"status": "aprovada", "total_centavos": 500, "origem": "contingencia", "terminal": "POS-01",
                "recebida_em": dados.para_utc_iso(chegada), "relogio_sincronizado": sincronizado,
                "criada_no_terminal": dados.para_utc_iso(chegada - timedelta(seconds=espera_s))}
    lista = [offline(60), offline(200), offline(600), offline(30, sincronizado=False)]
    celulas = dados.mapa_de_calor(lista)
    assert len(celulas) == 7 * 24
    quinta_meio_dia = [c for c in celulas if c["dia"] == "Qui" and c["hora"] == "12h"][0]   # 19/11/2026
    assert quinta_meio_dia["vendas"] == 4 and sum(c["vendas"] for c in celulas) == 4
    fila = dados.tempos_da_fila_offline(lista)
    assert fila["quantidade"] == 4 and fila["sem_relogio"] == 1
    assert fila["mediana_s"] == 200 and fila["maximo_s"] == 600
    assert dados.duracao(200) == "3 min 20 s" and dados.duracao(3725) == "1 h 02 min"
