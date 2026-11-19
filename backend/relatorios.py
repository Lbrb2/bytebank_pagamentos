"""
relatorios.py - Relatório do período para baixar (Excel e CSV)

GET /relatorios/exportar?formato=xlsx|csv&desde=...&terminal=...

  * Excel (.xlsx): uma planilha com cinco abas
        Resumo              faturamento, vendas, ticket médio, recusas, estornos
        Transações          todas as vendas do período (aprovadas, recusadas, estornadas)
        Mais vendidos       produtos por unidades vendidas
        Recusas por motivo  quantas recusas de cada tipo
        Alertas de fraude   cartões bloqueados pela antifraude
    Valores em reais e datas ficam como NÚMERO e DATA de verdade (com
    formato de exibição), então dá para somar, filtrar e fazer gráfico no
    Excel sem converter nada.

  * CSV: só as transações, no padrão do Excel em português: separador ";",
    vírgula decimal e codificação UTF-8 com BOM (para os acentos aparecerem
    certos ao abrir com duplo clique).

Os horários saem no fuso de Recife (UTC-3), como no painel.
"""

import csv
import io
from datetime import datetime, timedelta, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

FUSO_LOCAL = timezone(timedelta(hours=-3))

SITUACAO = {"aprovada": "Aprovada", "recusada": "Recusada", "estornada": "Estornada"}
ORIGEM = {"online": "Online", "contingencia": "Fila offline"}

FORMATO_REAIS = '"R$" #,##0.00'
FORMATO_DATA_HORA = "dd/mm/yyyy hh:mm:ss"
FORMATO_PORCENTAGEM = "0.0%"


def _para_local(texto_utc):
    """'2026-11-12T16:03:10Z' -> datetime local, sem fuso (o Excel não guarda fuso)."""
    momento = datetime.strptime(texto_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return momento.astimezone(FUSO_LOCAL).replace(tzinfo=None)


# ---------------------------------------------------------------- dados

def montar(servico, desde=None, terminal=None, agora=None):
    """Junta tudo o que vai no relatório, com os mesmos filtros do painel."""
    transacoes = list(reversed(servico.listar_transacoes(limite=1000000, desde=desde, terminal=terminal)))
    aprovadas = [t for t in transacoes if t["status"] == "aprovada"]
    recusadas = [t for t in transacoes if t["status"] == "recusada"]
    estornadas = [t for t in transacoes if t["status"] == "estornada"]
    faturamento = sum(t["total_centavos"] for t in aprovadas)

    recusas = {}
    for t in recusadas:
        recusas[t["motivo"] or "Sem motivo"] = recusas.get(t["motivo"] or "Sem motivo", 0) + 1

    agora = agora or datetime.now(FUSO_LOCAL)
    return {
        "periodo": "Desde {}".format(_para_local(desde).strftime("%d/%m/%Y %H:%M")) if desde else "Todo o período",
        "terminal": terminal or "Todos",
        "gerado_em": agora.astimezone(FUSO_LOCAL).replace(tzinfo=None, microsecond=0),
        "resumo": {
            "faturamento_centavos": faturamento,
            "vendas": len(aprovadas),
            "ticket_medio_centavos": faturamento // len(aprovadas) if aprovadas else 0,
            "offline": sum(1 for t in aprovadas if t["origem"] == "contingencia"),
            "recusadas": len(recusadas),
            "taxa_recusa": len(recusadas) / len(transacoes) if transacoes else 0.0,
            "estornos": len(estornadas),
            "estornado_centavos": sum(t["total_centavos"] for t in estornadas),
        },
        "transacoes": transacoes,
        "mais_vendidos": servico.mais_vendidos(9, desde, terminal),
        "recusas": sorted(recusas.items(), key=lambda par: (-par[1], par[0])),
        "alertas": [c for c in servico.cartoes_bloqueados()
                    if (c["motivo_bloqueio"] or "").startswith("Suspeita")],
    }


def nome_do_arquivo(formato, terminal=None, agora=None):
    agora = (agora or datetime.now(FUSO_LOCAL)).astimezone(FUSO_LOCAL)
    partes = ["bytebank_relatorio", terminal or "todos", agora.strftime("%Y-%m-%d_%Hh%M")]
    return "_".join(partes) + "." + formato


# ---------------------------------------------------------------- CSV

def _linha_transacao(t):
    cobrado = t["status"] != "recusada"
    return [
        _para_local(t["recebida_em"]), t["terminal"], t["id_transacao"],
        t["titular"] or "", t["uid_cartao"], SITUACAO[t["status"]], t["motivo"] or "",
        ORIGEM[t["origem"]], t["total_centavos"] / 100 if cobrado else None,
    ]


CABECALHO_TRANSACOES = ["Data e hora", "Terminal", "Venda", "Cliente", "Cartão", "Situação",
                        "Motivo da recusa", "Origem", "Valor (R$)"]


def para_csv(dados):
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(CABECALHO_TRANSACOES)
    for t in dados["transacoes"]:
        linha = _linha_transacao(t)
        linha[0] = linha[0].strftime("%d/%m/%Y %H:%M:%S")
        linha[-1] = "" if linha[-1] is None else "{:.2f}".format(linha[-1]).replace(".", ",")
        escritor.writerow(linha)
    return saida.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------- Excel

NEGRITO = Font(bold=True)
TITULO = Font(bold=True, size=14)
CINZA = Font(color="52514E")
FUNDO_CABECALHO = PatternFill("solid", fgColor="F0EFEC")
LINHA_CABECALHO = Border(bottom=Side(style="thin", color="C3C2B7"))


def _tabela(aba, linha_inicial, cabecalho, linhas, formatos=None, larguras=None):
    """Escreve cabeçalho + linhas a partir de linha_inicial, com filtro."""
    formatos = formatos or {}
    for coluna, texto in enumerate(cabecalho, start=1):
        celula = aba.cell(row=linha_inicial, column=coluna, value=texto)
        celula.font, celula.fill, celula.border = NEGRITO, FUNDO_CABECALHO, LINHA_CABECALHO
    for deslocamento, valores in enumerate(linhas, start=1):
        for coluna, valor in enumerate(valores, start=1):
            celula = aba.cell(row=linha_inicial + deslocamento, column=coluna, value=valor)
            if coluna in formatos:
                celula.number_format = formatos[coluna]
    ultima = linha_inicial + max(len(linhas), 1)
    aba.auto_filter.ref = "A{}:{}{}".format(linha_inicial, chr(64 + len(cabecalho)), ultima)
    for coluna, largura in enumerate(larguras or [], start=1):
        aba.column_dimensions[chr(64 + coluna)].width = largura


def para_excel(dados):
    livro = Workbook()

    # Resumo
    aba = livro.active
    aba.title = "Resumo"
    aba["A1"] = "ByteBank Pay · Relatório de vendas"
    aba["A1"].font = TITULO
    for linha, (rotulo, valor) in enumerate((("Período", dados["periodo"]), ("Terminal", dados["terminal"]),
                                             ("Gerado em", dados["gerado_em"])), start=3):
        aba.cell(row=linha, column=1, value=rotulo).font = CINZA
        celula = aba.cell(row=linha, column=2, value=valor)
        celula.alignment = Alignment(horizontal="left")
        if isinstance(valor, datetime):
            celula.number_format = FORMATO_DATA_HORA
    r = dados["resumo"]
    indicadores = [
        ("Faturamento", r["faturamento_centavos"] / 100, FORMATO_REAIS),
        ("Vendas aprovadas", r["vendas"], "0"),
        ("Ticket médio", r["ticket_medio_centavos"] / 100, FORMATO_REAIS),
        ("Vendas pela fila offline", r["offline"], "0"),
        ("Recusadas", r["recusadas"], "0"),
        ("Taxa de recusa", r["taxa_recusa"], FORMATO_PORCENTAGEM),
        ("Estornos", r["estornos"], "0"),
        ("Valor estornado", r["estornado_centavos"] / 100, FORMATO_REAIS),
    ]
    _tabela(aba, 7, ["Indicador", "Valor"], [(nome, valor) for nome, valor, _ in indicadores], larguras=[28, 18])
    for deslocamento, (_, _, formato) in enumerate(indicadores, start=1):
        aba.cell(row=7 + deslocamento, column=2).number_format = formato
    aba.auto_filter.ref = None

    # Transações
    aba = livro.create_sheet("Transações")
    _tabela(aba, 1, CABECALHO_TRANSACOES, [_linha_transacao(t) for t in dados["transacoes"]],
            formatos={1: FORMATO_DATA_HORA, 9: FORMATO_REAIS},
            larguras=[20, 10, 22, 26, 11, 11, 30, 13, 12])
    aba.freeze_panes = "A2"

    # Mais vendidos
    aba = livro.create_sheet("Mais vendidos")
    _tabela(aba, 1, ["Posição", "Código", "Produto", "Unidades", "Receita (R$)"],
            [(posicao, p["codigo"], p["nome"], p["unidades"], p["receita_centavos"] / 100)
             for posicao, p in enumerate(dados["mais_vendidos"], start=1)],
            formatos={5: FORMATO_REAIS}, larguras=[9, 8, 20, 10, 14])
    aba.freeze_panes = "A2"

    # Recusas por motivo
    aba = livro.create_sheet("Recusas por motivo")
    _tabela(aba, 1, ["Motivo", "Quantidade"], dados["recusas"], larguras=[34, 12])
    aba.freeze_panes = "A2"

    # Alertas de fraude
    aba = livro.create_sheet("Alertas de fraude")
    _tabela(aba, 1, ["Cartão", "Cliente", "Motivo", "Bloqueado em"],
            [(c["uid_rfid"], c["titular"], c["motivo_bloqueio"],
              _para_local(c["bloqueado_em"]) if c["bloqueado_em"] else None) for c in dados["alertas"]],
            formatos={4: FORMATO_DATA_HORA}, larguras=[11, 26, 52, 20])
    aba.freeze_panes = "A2"

    saida = io.BytesIO()
    livro.save(saida)
    return saida.getvalue()
