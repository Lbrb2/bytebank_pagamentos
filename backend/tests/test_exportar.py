"""Exportação do relatório (Excel e CSV)."""

import csv
import io
from datetime import datetime

from openpyxl import load_workbook

from backend.tests.conftest import venda


def preparar(cliente):
    cliente.post("/transacoes", json=venda("V1", itens=(("1", 2), ("2", 1))))          # R$ 13,00
    cliente.post("/transacoes", json=venda("V2", itens=(("6", 1),), origem="contingencia"))  # R$ 12,00
    cliente.post("/transacoes", json=venda("R1", uid="93892B4F", itens=(("6", 1),)))   # saldo insuficiente
    cliente.post("/transacoes", json=venda("V3", itens=(("1", 1),)))
    cliente.post("/estornos", json={"id_transacao": "V3", "terminal": "POS-01"})


def test_csv_no_padrao_do_excel_em_portugues(cliente):
    preparar(cliente)
    resposta = cliente.get("/relatorios/exportar?formato=csv")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/csv")
    assert 'filename="bytebank_relatorio_todos_' in resposta.headers["content-disposition"]
    assert resposta.content.startswith(b"\xef\xbb\xbf")                      # BOM: acentos certos no Excel
    linhas = list(csv.reader(io.StringIO(resposta.content.decode("utf-8-sig")), delimiter=";"))
    assert linhas[0] == ["Data e hora", "Terminal", "Venda", "Cliente", "Cartão", "Situação",
                         "Motivo da recusa", "Origem", "Valor (R$)"]
    por_venda = {linha[2]: linha for linha in linhas[1:]}
    assert [linha[2] for linha in linhas[1:]] == ["V1", "V2", "R1", "V3"]     # ordem cronológica
    assert por_venda["V1"][5:] == ["Aprovada", "", "Online", "13,00"]
    assert por_venda["V2"][7] == "Fila offline"
    assert por_venda["R1"][5:] == ["Recusada", "Saldo insuficiente", "Online", ""]
    assert por_venda["V3"][5] == "Estornada"
    datetime.strptime(por_venda["V1"][0], "%d/%m/%Y %H:%M:%S")


def test_excel_com_cinco_abas(cliente):
    preparar(cliente)
    resposta = cliente.get("/relatorios/exportar?formato=xlsx&terminal=POS-01")
    assert resposta.status_code == 200
    assert "filename=\"bytebank_relatorio_POS-01_" in resposta.headers["content-disposition"]
    livro = load_workbook(io.BytesIO(resposta.content))
    assert livro.sheetnames == ["Resumo", "Transações", "Mais vendidos", "Recusas por motivo", "Alertas de fraude"]

    resumo = {livro["Resumo"].cell(row=r, column=1).value: livro["Resumo"].cell(row=r, column=2)
              for r in range(1, 20)}
    assert resumo["Terminal"].value == "POS-01"
    assert resumo["Faturamento"].value == 25.0                          # 13 + 12 (o estorno não conta)
    assert resumo["Faturamento"].number_format == '"R$" #,##0.00'
    assert resumo["Vendas aprovadas"].value == 2
    assert resumo["Vendas pela fila offline"].value == 1
    assert resumo["Taxa de recusa"].value == 0.25 and resumo["Taxa de recusa"].number_format == "0.0%"
    assert resumo["Valor estornado"].value == 5.0

    transacoes = livro["Transações"]
    assert transacoes.freeze_panes == "A2"
    assert isinstance(transacoes["A2"].value, datetime)                 # data de verdade, não texto
    assert transacoes["I2"].value == 13.0 and transacoes["I2"].number_format == '"R$" #,##0.00'

    mais = livro["Mais vendidos"]
    assert [mais.cell(row=2, column=c).value for c in range(1, 6)] == [1, "1", "Café", 2, 10.0]
    assert livro["Recusas por motivo"]["A2"].value == "Saldo insuficiente"


def test_alertas_de_fraude_no_excel(cliente):
    for numero in range(4):
        cliente.post("/transacoes", json=venda("V{}".format(numero)))
    livro = load_workbook(io.BytesIO(cliente.get("/relatorios/exportar").content))
    alertas = livro["Alertas de fraude"]
    assert alertas["A2"].value == "532F07FE"
    assert alertas["C2"].value == "Suspeita de fraude: 4 compras em menos de 60 s"
    assert isinstance(alertas["D2"].value, datetime)


def test_periodo_sem_vendas_e_formato_invalido(cliente):
    livro = load_workbook(io.BytesIO(cliente.get("/relatorios/exportar?desde=2099-01-01T00:00:00Z").content))
    assert livro["Resumo"]["B3"].value == "Desde 31/12/2098 21:00"
    assert livro["Transações"].max_row == 1
    assert cliente.get("/relatorios/exportar?formato=pdf").status_code == 400
