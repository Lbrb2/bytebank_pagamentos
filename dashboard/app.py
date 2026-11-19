"""
app.py - Painel do gerente do ByteBank Pay (Streamlit)

Da pasta raiz do repositório, com o backend rodando (python -m backend):

    pip install -r dashboard/requirements.txt
    streamlit run dashboard/app.py

O painel se atualiza sozinho a cada 5 segundos e conversa SÓ com a API
(nunca com o banco direto). Ele mostra as vendas e o estado dos terminais
em tempo real e manda comandos para o ESP32 (mensagem, bloquear,
desbloquear, atualizar catálogo), que chegam na próxima consulta da placa.

Organização da tela:
    filtros + exportar            (valem para tudo abaixo)
    alertas de fraude             (só quando há)
    números principais            (com a comparação com o período anterior)
    abas: Vendas | Estoque | Terminais | Controle remoto | Placa (USB)
"""

import os
import sys
from datetime import timedelta
from html import escape

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dados  # noqa: E402
import graficos  # noqa: E402
import placa_usb  # noqa: E402

URL_PADRAO = os.environ.get("BYTEBANK_API_URL", "http://127.0.0.1:8000")
INTERVALO_S = 5

st.set_page_config(page_title="ByteBank Pay · Painel", page_icon="💳", layout="wide")


def tema_atual():
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:   # Streamlit antigo: sem detecção de tema
        return "light"


TEMA = tema_atual()

# Cor do texto da variação: verde = melhorou, vermelho = piorou (com seta e sinal junto)
COR_VARIACAO = {
    "light": {"bom": "#006300", "ruim": "#d03b3b"},
    "dark": {"bom": "#0ca30c", "ruim": "#e66767"},
}[TEMA]

# Texto herda a cor do tema do Streamlit (claro ou escuro); os tons
# secundário e suave são a mesma cor com menos opacidade.
st.markdown("""
<style>
.bb-rotulo { opacity: 0.78; font-size: 0.9rem; margin-bottom: 0.15rem; }
.bb-heroi { font-size: clamp(2rem, 3.4vw, 3.25rem); font-weight: 600; line-height: 1.1; white-space: nowrap; }
.bb-valor { font-size: 1.9rem; font-weight: 600; line-height: 1.2; }
.bb-sub { opacity: 0.66; font-size: 0.85rem; margin-top: 0.1rem; }
.bb-variacao { font-size: 0.9rem; font-weight: 600; margin-top: 0.15rem; }
.bb-terminal { border: 1px solid rgba(128, 128, 128, 0.3); border-radius: 8px;
               padding: 0.75rem 1rem; margin-bottom: 0.5rem; }
.bb-chip { font-size: 0.95rem; margin-left: 0.5rem; white-space: nowrap; }
.bb-ponto { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
            margin-right: 0.35rem; vertical-align: 0; }
.bb-detalhes { opacity: 0.78; font-size: 0.85rem; margin-top: 0.35rem; }
.bb-detalhes span { white-space: nowrap; }
.bb-vazio { opacity: 0.66; font-size: 0.95rem; padding: 2.5rem 0; text-align: center; }
.bb-estoque { display: grid; grid-template-columns: minmax(7rem, 1.2fr) 4rem 4.5rem minmax(6rem, 2fr) minmax(12rem, 1.7fr);
              gap: 0.4rem 1rem; align-items: center; }
.bb-estoque .cab { opacity: 0.66; font-size: 0.8rem; }
.bb-estoque .num { text-align: right; font-variant-numeric: tabular-nums; }
.bb-trilha { height: 8px; border-radius: 4px; overflow: hidden; }
.bb-preenchido { height: 8px; border-radius: 4px; }
@media (max-width: 640px) {
  .bb-estoque { grid-template-columns: 1fr 3.5rem 3.5rem; }
  .bb-estoque .trilha-col, .bb-estoque .cab-trilha, .bb-estoque .prev, .bb-estoque .cab-prev { grid-column: 1 / -1; }
}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- pedaços de tela

def bloco_numero(rotulo, valor, sub=None, heroi=False, variacao=None):
    classe = "bb-heroi" if heroi else "bb-valor"
    html = '<div class="bb-rotulo">{}</div><div class="{}">{}</div>'.format(
        escape(rotulo), classe, escape(valor))
    if variacao:
        texto, tom = variacao
        estilo = 'style="color:{}"'.format(COR_VARIACAO[tom]) if tom in COR_VARIACAO else 'style="opacity:0.66"'
        html += '<div class="bb-variacao" {}>{}</div>'.format(estilo, escape(texto))
    if sub:
        html += '<div class="bb-sub">{}</div>'.format(escape(sub))
    st.markdown(html, unsafe_allow_html=True)


def chip_situacao(nivel, rotulo):
    estilo = graficos.SITUACAO[nivel]
    return '<span class="bb-chip"><span class="bb-ponto" style="background:{}"></span>{} {}</span>'.format(
        estilo["cor"], estilo["icone"], escape(rotulo))


def vazio(texto):
    st.markdown('<div class="bb-vazio">{}</div>'.format(escape(texto)), unsafe_allow_html=True)


def tabela(linhas, colunas):
    """Tabela equivalente ao gráfico (acessível sem mouse)."""
    st.dataframe(pd.DataFrame(linhas)[list(colunas)].rename(columns=colunas),
                 hide_index=True, width="stretch")


def aviso_de_erro(erro):
    st.warning("! Não foi possível atualizar: {}. Tentando de novo em {} s.".format(erro, INTERVALO_S))


SITUACAO_VENDA = {"aprovada": "✓ Aprovada", "recusada": "✕ Recusada", "estornada": "↺ Estornada"}
ORIGEM = {"online": "Online", "contingencia": "Fila offline"}


# ---------------------------------------------------------------- cabeçalho e filtros

with st.sidebar:
    st.header("Configuração")
    url = st.text_input("Endereço do backend", URL_PADRAO,
                        help="O mesmo API_URL do config.py do ESP32.")
    automatico = st.toggle("Atualizar a cada {} s".format(INTERVALO_S), value=True)
    st.caption("Horários no fuso de Recife (UTC−3).")

api = dados.ClienteAPI(url)
ATUALIZAR = INTERVALO_S if automatico else None

st.title("ByteBank Pay · Painel do gerente")

try:
    lista_terminais = api.terminais()
except dados.ErroAPI as erro:
    st.error("✕ Sem conexão com o backend: {}.  \nNo computador do servidor, rode "
             "`python -m backend` e confira o endereço na barra lateral.".format(erro))
    st.stop()

# Uma linha de filtros, acima de tudo o que eles filtram (e a exportação usa os mesmos)
filtro_periodo, filtro_terminal, exportacao = st.columns([3, 1.6, 1.4], vertical_alignment="bottom")
with filtro_periodo:
    periodo = st.segmented_control("Período", dados.PERIODOS, default="Hoje") or "Hoje"
with filtro_terminal:
    codigos = [t["codigo"] for t in lista_terminais]
    escolha = st.selectbox("Terminal", ["Todos"] + codigos)
terminal = None if escolha == "Todos" else escolha


def desde_do_periodo():
    inicio = dados.inicio_do_periodo(periodo)
    return dados.para_utc_iso(inicio) if inicio else None


def baixar(formato):
    """Gera o arquivo só na hora do clique (não a cada atualização da tela)."""
    def gerar():
        return api.exportar(formato, desde_do_periodo(), terminal)
    return gerar


with exportacao:
    with st.popover("Exportar relatório", width="stretch"):
        st.caption("Período e terminal escolhidos ao lado.")
        momento = dados.agora_local().strftime("%Y-%m-%d_%Hh%M")
        nome = "bytebank_relatorio_{}_{}".format(terminal or "todos", momento)
        st.download_button("Excel (.xlsx)", data=baixar("xlsx"), file_name=nome + ".xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           on_click="ignore", width="stretch",
                           help="Resumo, transações, mais vendidos, recusas e alertas, uma aba para cada.")
        st.download_button("CSV (transações)", data=baixar("csv"), file_name=nome + ".csv",
                           mime="text/csv", on_click="ignore", width="stretch",
                           help="Abre no Excel e no Google Planilhas (separador ;).")


# ---------------------------------------------------------------- alertas e números principais

@st.fragment(run_every=ATUALIZAR)
def topo():
    try:
        transacoes = api.transacoes(desde=desde_do_periodo(), terminal=terminal)
        alertas = dados.alertas_de_fraude(api.cartoes_bloqueados())
        anterior = dados.periodo_anterior(periodo)
        transacoes_antes = (api.transacoes(desde=dados.para_utc_iso(anterior[0]),
                                           ate=dados.para_utc_iso(anterior[1]), terminal=terminal)
                            if anterior else None)
    except dados.ErroAPI as erro:
        aviso_de_erro(erro)
        return

    # Alertas de segurança: cartões bloqueados automaticamente pela antifraude
    if alertas:
        with st.container(border=True):
            st.markdown("**{} {}**".format(
                chip_situacao("critico", "Alerta de segurança"),
                "· 1 cartão bloqueado por segurança" if len(alertas) == 1
                else "· {} cartões bloqueados por segurança".format(len(alertas))),
                unsafe_allow_html=True)
            for cartao in alertas:
                texto, botao = st.columns([5, 1], vertical_alignment="center")
                quando = (dados.para_local(cartao["bloqueado_em"]).strftime("%H:%M:%S")
                          if cartao["bloqueado_em"] else "")
                texto.markdown("**Cartão {}** · {} · bloqueado às {}  \n{}".format(
                    cartao["uid_rfid"], cartao["titular"], quando,
                    "Motivo: " + dados.motivo_do_alerta(cartao["motivo_bloqueio"])))
                if botao.button("Desbloquear", key="desbloquear_" + cartao["uid_rfid"],
                                help="Libera o cartão depois de confirmar com o cliente."):
                    try:
                        api.desbloquear_cartao(cartao["uid_rfid"])
                    except dados.ErroAPI as erro:
                        st.error("✕ Não desbloqueado: {}".format(erro))
                    else:
                        st.rerun(scope="fragment")

    numeros = dados.indicadores(transacoes)
    antes = dados.indicadores(transacoes_antes) if transacoes_antes is not None else None

    def comparar(campo, para_cima_e_bom=True, em_porcentagem=True):
        if antes is None:
            return None
        return dados.variacao(numeros[campo], antes[campo], para_cima_e_bom, em_porcentagem)

    heroi, c1, c2, c3, c4 = st.columns([1.7, 1, 1, 1, 1])
    with heroi:
        bloco_numero("Faturamento · {}".format(periodo.lower()), dados.reais(numeros["faturamento_centavos"]),
                     heroi=True, variacao=comparar("faturamento_centavos"))
    with c1:
        bloco_numero("Vendas aprovadas", "{}".format(numeros["vendas"]),
                     "{} pela fila offline".format(numeros["offline"]) if numeros["offline"] else None,
                     variacao=comparar("vendas"))
    with c2:
        bloco_numero("Ticket médio", dados.reais(numeros["ticket_medio_centavos"]),
                     variacao=comparar("ticket_medio_centavos"))
    with c3:
        bloco_numero("Recusadas", "{}".format(numeros["recusadas"]),
                     "{:.0%} das tentativas".format(numeros["taxa_recusa"]) if numeros["recusadas"] else None,
                     variacao=comparar("recusadas", para_cima_e_bom=False, em_porcentagem=False))
    with c4:
        bloco_numero("Estornos", "{}".format(numeros["estornos"]),
                     dados.reais(numeros["estornado_centavos"]) if numeros["estornos"] else None,
                     variacao=comparar("estornos", para_cima_e_bom=False, em_porcentagem=False))
    if antes is not None:
        st.caption("Variação em relação a {}. ▲ subiu · ▼ caiu · verde melhorou · vermelho piorou.".format(
            anterior[2]))


topo()

aba_vendas, aba_estoque, aba_terminais, aba_controle, aba_placa = st.tabs(
    ["Vendas", "Estoque", "Terminais", "Controle remoto", "Placa (USB)"])


# ---------------------------------------------------------------- aba Vendas

@st.fragment(run_every=ATUALIZAR)
def vendas():
    desde = desde_do_periodo()
    try:
        transacoes = api.transacoes(desde=desde, terminal=terminal)
        mais_vendidos = api.mais_vendidos(desde=desde, terminal=terminal)
    except dados.ErroAPI as erro:
        aviso_de_erro(erro)
        return
    numeros = dados.indicadores(transacoes)

    esquerda, direita = st.columns([1.6, 1])
    linhas_tempo, granularidade = dados.faturamento_no_tempo(transacoes, periodo)
    with esquerda:
        st.subheader("Faturamento por {}".format(granularidade))
        if numeros["vendas"]:
            st.altair_chart(graficos.faturamento_no_tempo(linhas_tempo, granularidade, TEMA), width="stretch")
        else:
            vazio("Nenhuma venda aprovada no período.")
        with st.expander("Ver tabela"):
            tabela([dict(l, faturamento_txt=dados.reais(l["faturamento_centavos"])) for l in linhas_tempo],
                   {"rotulo": granularidade.capitalize(), "vendas": "Vendas", "faturamento_txt": "Faturamento"})
    with direita:
        st.subheader("Mais vendidos")
        if mais_vendidos:
            for produto in mais_vendidos:
                produto["receita_txt"] = dados.reais(produto["receita_centavos"])
            st.altair_chart(graficos.ranking(mais_vendidos, "nome", "unidades", "Unidades", TEMA,
                                             extras=[("receita_txt", "Receita")]), width="stretch")
            with st.expander("Ver tabela"):
                tabela(mais_vendidos, {"nome": "Produto", "unidades": "Unidades", "receita_txt": "Receita"})
        else:
            vazio("Nenhum produto vendido no período.")

    st.subheader("Horário de pico")
    calor = dados.mapa_de_calor(transacoes)
    if periodo in ("Hoje", "Últimas 24 h"):
        # Com um dia só, o gráfico de faturamento por hora (acima) já mostra o pico
        vazio("Escolha \"Últimos 7 dias\" ou \"Tudo\" para comparar os dias da semana.")
    elif numeros["vendas"]:
        st.caption("Vendas aprovadas por dia da semana e hora: quanto mais forte o azul, mais vendas.")
        st.altair_chart(graficos.mapa_de_calor(calor, TEMA), width="stretch")
        with st.expander("Ver tabela"):
            tabela([l for l in calor if l["vendas"]],
                   {"dia": "Dia", "hora": "Hora", "vendas": "Vendas", "faturamento_txt": "Faturamento"})
    else:
        vazio("Nenhuma venda aprovada no período.")

    recusas = dados.recusas_por_motivo(transacoes)
    esquerda, direita = st.columns([1.6, 1])
    with esquerda:
        st.subheader("Últimas transações")
        if transacoes:
            st.dataframe(pd.DataFrame([{
                "Hora": dados.para_local(t["recebida_em"]).strftime("%d/%m %H:%M:%S"),
                "Terminal": t["terminal"],
                "Cliente": t["titular"] or "Cartão {}".format(t["uid_cartao"]),
                "Valor": dados.reais(t["total_centavos"]) if t["status"] != "recusada" else "—",
                "Situação": SITUACAO_VENDA[t["status"]],
                "Motivo": t["motivo"] or "",
                "Origem": ORIGEM[t["origem"]],
            } for t in transacoes[:15]]), hide_index=True, width="stretch")
        else:
            vazio("Nenhuma transação no período.")
    with direita:
        st.subheader("Recusas por motivo")
        if recusas:
            st.altair_chart(graficos.ranking(recusas, "motivo", "quantidade", "Recusas", TEMA), width="stretch")
            with st.expander("Ver tabela"):
                tabela(recusas, {"motivo": "Motivo", "quantidade": "Recusas"})
        else:
            vazio("Nenhuma recusa no período.")

    st.caption("Atualizado às {}{}.".format(
        dados.agora_local().strftime("%H:%M:%S"),
        " · atualiza a cada {} s".format(INTERVALO_S) if automatico else ""))


with aba_vendas:
    vendas()


# ---------------------------------------------------------------- aba Estoque

COR_NIVEL = {"bom": graficos.TEMAS[TEMA]["serie"], "atencao": graficos.SITUACAO["atencao"]["cor"],
             "critico": graficos.SITUACAO["critico"]["cor"]}
ROTULO_NIVEL = {"bom": "Ok", "atencao": "Atenção", "critico": "Repor já"}
HORAS_NA_BARRA = 8    # a barra mostra a autonomia até 8 h (cheia = 8 h ou mais)


@st.fragment(run_every=ATUALIZAR)
def estoque():
    try:
        produtos = api.produtos()
        ultima_hora = api.mais_vendidos(desde=dados.para_utc_iso(dados.agora_local() - timedelta(hours=1)),
                                        k=9)
    except dados.ErroAPI as erro:
        aviso_de_erro(erro)
        return
    previsao = dados.previsao_de_estoque(produtos, ultima_hora)
    st.subheader("Estoque e previsão")
    st.caption("A barra mostra quanto tempo o estoque dura no ritmo de vendas da última hora, somando "
               "todos os terminais (cheia = {} h ou mais). Sem vendas na última hora, não há previsão "
               "e a barra fica vazia.".format(HORAS_NA_BARRA))
    partes = ['<div class="bb-estoque">',
              '<div class="cab">Produto</div><div class="cab num">Estoque</div>'
              '<div class="cab num">Vendas/h</div>'
              '<div class="cab cab-trilha">Autonomia</div><div class="cab cab-prev">Previsão</div>']
    for linha in previsao:
        cor = COR_NIVEL[linha["nivel"]]
        if linha["horas"] is None:
            trilha = '<div class="bb-trilha" style="background:rgba(128,128,128,0.15)"></div>'
        else:
            trilha = ('<div class="bb-trilha" style="background:{cor}33"><div class="bb-preenchido" '
                      'style="width:{pct:.0f}%;background:{cor}"></div></div>').format(
                cor=cor, pct=min(linha["horas"] / HORAS_NA_BARRA, 1) * 100)
        partes.append(
            '<div>{nome}</div><div class="num">{estoque}</div><div class="num">{ritmo}</div>'
            '<div class="trilha-col">{trilha}</div>'
            '<div class="prev">{chip} <span style="opacity:0.78">{previsao}</span></div>'.format(
                nome=escape(linha["nome"]), estoque=linha["estoque"], ritmo=linha["por_hora"],
                trilha=trilha, chip=chip_situacao(linha["nivel"], ROTULO_NIVEL[linha["nivel"]]),
                previsao=escape(linha["previsao"])))
    partes.append("</div>")
    st.markdown("".join(partes), unsafe_allow_html=True)


ACOES_ESTOQUE = {
    "Chegou mercadoria": "Soma ao estoque atual",
    "Corrigir contagem": "Troca o total, depois de contar",
    "Mudar preço": "Vale da próxima venda em diante",
}

with aba_estoque:
    estoque()
    st.divider()
    st.subheader("Repor estoque ou mudar preço")
    try:
        produtos_form = api.produtos()
    except dados.ErroAPI as erro:
        produtos_form = []
        st.error("✕ {}".format(erro))
    if produtos_form:
        nomes = {"{} · {}".format(p["codigo"], p["nome"]): p for p in produtos_form}
        escolhido = nomes[st.selectbox("Produto", list(nomes), key="produto_form")]
        codigo_produto = escolhido["codigo"]
        acao_estoque = st.radio("O que fazer", list(ACOES_ESTOQUE), captions=list(ACOES_ESTOQUE.values()),
                                horizontal=True, key="acao_estoque")

        # O estoque que o gerente VIU fica guardado na sessão: cada clique roda o
        # script de novo e, sem isso, a conferência compararia com o valor novo.
        visto = st.session_state.get("estoque_visto")
        if not visto or visto[0] != codigo_produto:
            visto = st.session_state["estoque_visto"] = (codigo_produto, escolhido["estoque"])

        campo_col, _ = st.columns([1, 2])
        if acao_estoque == "Chegou mercadoria":
            unidades = int(campo_col.number_input("Unidades que chegaram", min_value=1, max_value=10000,
                                                  step=1, value=10, key="unidades_" + codigo_produto))
            st.caption("Agora: {} un. → depois de salvar: {} un. Vendas feitas enquanto você digita "
                       "continuam descontadas, porque o banco soma ao valor do momento.".format(
                           escolhido["estoque"], escolhido["estoque"] + unidades))
        elif acao_estoque == "Corrigir contagem":
            total = int(campo_col.number_input("Total contado na prateleira", min_value=0, step=1,
                                               value=visto[1], key="total_" + codigo_produto))
            st.caption("No sistema quando você abriu: {} un. Se houver venda antes de salvar, o painel "
                       "avisa em vez de apagar a venda da conta.".format(visto[1]))
        else:
            preco = campo_col.number_input("Preço novo (R$)", min_value=0.01, step=0.50, format="%.2f",
                                           value=escolhido["preco_centavos"] / 100, key="preco_" + codigo_produto)
            st.caption("Hoje: {}. Vale a partir da próxima venda; as antigas guardam o preço "
                       "da época.".format(dados.reais(escolhido["preco_centavos"])))
        avisar = st.checkbox("Avisar os terminais (atualizar o catálogo na tela)", value=True)

        if st.button("Salvar", type="primary"):
            try:
                if acao_estoque == "Chegou mercadoria":
                    produto = api.repor_estoque(codigo_produto, unidades)
                    feito = "+{} {}: estoque agora {} un.".format(unidades, produto["nome"], produto["estoque"])
                elif acao_estoque == "Corrigir contagem":
                    produto = api.alterar_produto(codigo_produto, estoque=total, estoque_esperado=visto[1])
                    feito = "{}: estoque corrigido para {} un.".format(produto["nome"], produto["estoque"])
                else:
                    produto = api.alterar_produto(codigo_produto, preco_centavos=int(round(preco * 100)))
                    feito = "{} agora custa {}.".format(produto["nome"], dados.reais(produto["preco_centavos"]))
                st.session_state.pop("estoque_visto", None)
                if avisar:
                    for codigo in codigos:
                        api.enviar_comando(codigo, "atualizar_catalogo")
                st.success("✓ " + feito + (" Terminais avisados." if avisar and codigos else ""))
            except dados.ErroAPI as erro:
                if erro.codigo_http == 409:
                    st.session_state.pop("estoque_visto", None)     # a próxima tela já mostra o valor novo
                    st.warning("⚠ Não salvo. {} Se a sua contagem foi feita depois dessa venda, "
                               "é só clicar em Salvar de novo.".format(erro))
                else:
                    st.error("✕ Não salvo: {}".format(erro))


# ---------------------------------------------------------------- aba Terminais

@st.fragment(run_every=ATUALIZAR)
def terminais_e_saude():
    desde = desde_do_periodo()
    try:
        terminais = [t for t in api.terminais() if terminal in (None, t["codigo"])]
        telemetria = api.telemetria(desde=desde, terminal=terminal)
        transacoes = api.transacoes(desde=desde, terminal=terminal)
    except dados.ErroAPI as erro:
        aviso_de_erro(erro)
        return

    # Situação agora: ícone + texto, nunca só a cor
    st.subheader("Situação agora")
    colunas = st.columns(max(1, min(3, len(terminais))))
    for posicao, item in enumerate(terminais):
        nivel, rotulo = dados.situacao_do_terminal(item)
        ultima = item.get("ultima_telemetria") or {}
        detalhes = []
        if ultima.get("rssi") is not None:
            detalhes.append("Sinal {} dBm".format(ultima["rssi"]).replace("-", "−"))
        if ultima.get("memoria_livre") is not None:
            detalhes.append("Memória livre {} KB".format(ultima["memoria_livre"] // 1024))
        if ultima.get("fila_pendente") is not None:
            detalhes.append("Fila offline {}".format(ultima["fila_pendente"]))
        if item["status"] == "bloqueado" and item.get("motivo_bloqueio"):
            detalhes.append("Motivo: {}".format(item["motivo_bloqueio"]))
        with colunas[posicao % len(colunas)]:
            st.markdown('<div class="bb-terminal"><b>{}</b>{}<div class="bb-detalhes">{}</div></div>'.format(
                escape(item["codigo"]), chip_situacao(nivel, rotulo),
                " · ".join("<span>{}</span>".format(escape(d)) for d in detalhes) or "Ainda sem telemetria"),
                unsafe_allow_html=True)

    # Saúde ao longo do tempo: um gráfico por medida (escalas diferentes, nunca dois eixos)
    st.subheader("Saúde ao longo do tempo")
    serie = dados.serie_de_telemetria(telemetria, periodo)
    granularidade = "5min" if periodo in ("Hoje", "Últimas 24 h") else "hora"
    com_dados = sorted({l["terminal"] for l in serie})
    if not com_dados:
        vazio("Nenhuma telemetria no período. A placa envia uma a cada 60 s.")
    for codigo in com_dados:
        linhas = [l for l in serie if l["terminal"] == codigo]
        st.markdown("**{}**".format(escape(codigo)))
        sinal_col, memoria_col = st.columns(2)
        with sinal_col:
            st.caption("Sinal do Wi-Fi (dBm, quanto mais perto de zero, melhor)")
            grafico = graficos.linha_no_tempo(linhas, "rssi", "Sinal (dBm)", TEMA, "{:.0f}", granularidade,
                                              amplitude_minima=20)
            if grafico is not None:
                st.altair_chart(grafico, width="stretch")
        with memoria_col:
            st.caption("Memória livre na placa (KB)")
            grafico = graficos.linha_no_tempo(linhas, "memoria_kb", "Memória livre (KB)", TEMA, "{:.0f}",
                                              granularidade, amplitude_minima=20)
            if grafico is not None:
                st.altair_chart(grafico, width="stretch")
        alerta = dados.tendencia_de_memoria(linhas)
        if alerta:
            st.markdown(chip_situacao("atencao", alerta), unsafe_allow_html=True)
        with st.expander("Ver tabela ({})".format(codigo)):
            tabela([dict(l, momento=l["momento"].strftime("%d/%m %H:%M")) for l in linhas],
                   {"momento": "Horário", "rssi": "Sinal (dBm)", "memoria_kb": "Memória livre (KB)",
                    "fila": "Fila offline"})

    # Fila offline: quanto as vendas sem conexão esperaram até chegar
    st.subheader("Fila offline")
    fila = dados.tempos_da_fila_offline(transacoes)
    if not fila["quantidade"]:
        vazio("Nenhuma venda pela fila offline no período.")
        return
    c1, c2, c3, _ = st.columns([1, 1, 1, 1])
    with c1:
        bloco_numero("Vendas pela fila offline", str(fila["quantidade"]))
    with c2:
        bloco_numero("Espera típica até chegar", dados.duracao(fila["mediana_s"]) if fila["medidas"] else "—",
                     "mediana")
    with c3:
        bloco_numero("Maior espera", dados.duracao(fila["maximo_s"]) if fila["medidas"] else "—")
    if fila["sem_relogio"]:
        st.caption("{} venda(s) feitas com o relógio da placa ainda sem acertar ficaram fora da conta.".format(
            fila["sem_relogio"]))
    if fila["medidas"]:
        with st.expander("Ver tabela"):
            st.dataframe(pd.DataFrame([{
                "Venda no terminal": m["venda"].strftime("%d/%m %H:%M:%S"),
                "Chegou ao servidor": m["chegada"].strftime("%d/%m %H:%M:%S"),
                "Espera": dados.duracao(m["espera_s"]),
                "Terminal": m["terminal"],
                "Situação": SITUACAO_VENDA[m["status"]],
                "Valor": dados.reais(m["total_centavos"]),
            } for m in sorted(fila["medidas"], key=lambda m: m["venda"], reverse=True)]),
                hide_index=True, width="stretch")


with aba_terminais:
    terminais_e_saude()


# ---------------------------------------------------------------- aba Controle remoto

ACOES = {
    "Mostrar mensagem": "mensagem",
    "Bloquear terminal": "bloquear",
    "Desbloquear terminal": "desbloquear",
    "Atualizar catálogo": "atualizar_catalogo",
}


@st.fragment(run_every=ATUALIZAR)
def comandos_recentes():
    try:
        comandos = api.comandos(limite=6)
    except dados.ErroAPI:
        st.caption("Não foi possível carregar os comandos agora.")
        return
    if not comandos:
        st.caption("Nenhum comando enviado ainda. Cada comando aparece aqui como "
                   "\"… Aguardando o terminal\" e muda para \"✓ Executado\" quando a placa confirma.")
    else:
        st.caption("Últimos comandos")
        st.dataframe(pd.DataFrame([{
            "Hora": dados.para_local(c["criado_em"]).strftime("%H:%M"),
            "Terminal": c["terminal"],
            "Comando": COMANDOS_NA_TABELA.get(c["tipo"], c["tipo"]),
            "Situação": situacao_do_comando(c),
        } for c in comandos]), hide_index=True, width="stretch",
            column_config={"Hora": st.column_config.TextColumn(width="small"),
                           "Terminal": st.column_config.TextColumn(width="small")})


COMANDOS_NA_TABELA = {"mensagem": "Mensagem", "bloquear": "Bloquear", "desbloquear": "Desbloquear",
                      "atualizar_catalogo": "Atualizar catálogo"}


def situacao_do_comando(comando):
    """'ok: 8 produtos' -> '✓ 8 produtos'; 'erro: ...' -> '✕ ...'. Ícone + texto, nunca só cor."""
    if comando["status"] != "executado":
        return "… Aguardando"
    tipo, _, detalhe = (comando["resultado"] or "ok").partition(":")
    icone = {"ok": "✓", "erro": "✕"}.get(tipo.strip(), "–")
    texto = detalhe.strip() or {"✓": "Executado", "✕": "Erro"}.get(icone, tipo.strip())
    return "{} {}".format(icone, texto[:1].upper() + texto[1:])


with aba_controle:
    comando_col, historico_col = st.columns(2)
    with comando_col:
        st.subheader("Comando para o terminal")
        alvo = st.selectbox("Terminal", codigos or ["POS-01"], key="alvo_comando")
        acao = st.radio("Ação", list(ACOES), horizontal=True)
        parametros = {}
        if acao == "Mostrar mensagem":
            parametros["texto"] = st.text_input("Texto (até 96 caracteres)", max_chars=96,
                                                placeholder="Promoção: café a R$ 4,00")
            parametros["segundos"] = st.slider("Tempo na tela (s)", 3, 60, 10)
        elif acao == "Bloquear terminal":
            parametros["motivo"] = st.text_input("Motivo (aparece na tela)", "Fale com o gerente", max_chars=96)
        if st.button("Enviar comando", type="primary"):
            try:
                criado = api.enviar_comando(alvo, ACOES[acao], **parametros)
                st.success("✓ Comando {} enviado. O terminal executa na próxima consulta (até 10 s).".format(
                    criado["id"]))
            except dados.ErroAPI as erro:
                st.error("✕ Não enviado: {}".format(erro))
    with historico_col:
        st.subheader("Acompanhamento")
        comandos_recentes()

    with st.expander("Auditoria (últimos 30 registros)"):
        try:
            registros = api.auditoria(limite=30)
            st.dataframe(pd.DataFrame([{
                "Hora": dados.para_local(r["criado_em"]).strftime("%d/%m %H:%M:%S"),
                "Evento": r["evento"].replace("_", " "),
                "Terminal": r["terminal"] or "",
                "Antes": r["valor_anterior"] if r["valor_anterior"] is not None else "",
                "Depois": r["valor_novo"] if r["valor_novo"] is not None else "",
                "Detalhes": r["detalhes"] or "",
            } for r in registros]), hide_index=True, width="stretch")
        except dados.ErroAPI as erro:
            st.error("✕ {}".format(erro))


# ---------------------------------------------------------------- aba Placa (USB)

def acesso_local():
    """A aba mexe no USB DESTE computador: só quem está nele pode usar.
    Se o painel foi aberto de outro computador da rede, a aba fica travada."""
    import ipaddress
    try:
        ip = st.context.ip_address
    except Exception:       # Streamlit antigo: sem como saber; libera
        return True
    if not isinstance(ip, str):     # sem informação (ex.: testes automáticos)
        return True
    try:
        endereco = ipaddress.ip_address(ip)
    except ValueError:
        return ip == "localhost"
    mapeado = getattr(endereco, "ipv4_mapped", None)       # ::ffff:127.0.0.1
    return endereco.is_loopback or bool(mapeado and mapeado.is_loopback)


def mostrar_verificacoes(linhas):
    for nivel, texto in linhas:
        (st.success if nivel == "bom" else st.error)(("✓ " if nivel == "bom" else "✕ ") + texto)


with aba_placa:
    st.subheader("Placa ligada no USB deste computador")
    st.caption("Faz pelo painel o que hoje é feito no Thonny: configurar o Wi-Fi e o endereço do backend, "
               "enviar o código e ver o console. **Feche o Thonny antes**: a porta USB só atende um "
               "programa por vez.")
    if not acesso_local():
        st.info("Esta aba só funciona no computador onde a placa está ligada. Abra o painel nele, "
                "em http://localhost:8501.")
    elif not placa_usb.mpremote_instalado():
        st.warning("⚠ Falta instalar o mpremote neste computador:  `pip install mpremote`  "
                   "e reinicie o painel.")
    else:
        placas = placa_usb.listar_placas()
        porta_col, atualizar_col = st.columns([3, 1], vertical_alignment="bottom")
        with atualizar_col:
            st.button("Procurar de novo", key="procurar_placa", width="stretch")
        if not placas:
            with porta_col:
                st.info("Nenhuma placa encontrada no USB. Confira o cabo (alguns só carregam e não "
                        "passam dados) e o driver do chip USB da placa (CH340 ou CP210x).")
        else:
            with porta_col:
                rotulos = {"{} · {}".format(p["porta"], p["chip"]): p["porta"] for p in placas}
                porta = rotulos[st.selectbox("Porta", list(rotulos), key="porta_placa")]
            placa = placa_usb.Placa(porta)

            # 1. Configurar ------------------------------------------------
            st.markdown("##### 1. Wi-Fi e endereço do backend (config.py)")
            atual = st.session_state.get("config_lido", {})
            if st.button("Ler o config.py da placa", key="ler_config"):
                try:
                    with st.spinner("Lendo a placa..."):
                        atual = st.session_state["config_lido"] = placa.ler_config()
                    st.success("✓ Lido." if atual else "A placa ainda não tem config.py.")
                except placa_usb.ErroPlaca as erro:
                    st.error("✕ {}".format(erro))
            with st.form("form_config"):
                rede_col, senha_col = st.columns(2)
                ssid = rede_col.text_input("Nome da rede Wi-Fi (2,4 GHz)", atual.get("WIFI_SSID", ""))
                senha_wifi = senha_col.text_input(
                    "Senha do Wi-Fi", type="password",
                    placeholder="em branco: mantém a da placa" if atual.get("WIFI_SENHA") else "")
                url_col, terminal_col = st.columns(2)
                api_url = url_col.text_input(
                    "Endereço do backend para a placa", placa_usb.api_url_para_a_placa(url),
                    help="O IP deste computador na rede, que a placa consegue alcançar "
                         "(não serve 127.0.0.1: para a placa, esse endereço é ela mesma).")
                terminal_id = terminal_col.text_input("Terminal", atual.get("TERMINAL_ID", "POS-01"))
                pino_col, pin_col = st.columns(2)
                pino = pino_col.selectbox("Botão", [32, 0], index=0 if atual.get("PINO_BOTAO", 32) == 32 else 1,
                                          format_func=lambda p: {32: "32 · botão no protoboard",
                                                                 0: "0 · botão BOOT da placa"}[p])
                pin_operador = pin_col.text_input(
                    "PIN do modo operador (4 a 8 dígitos)", type="password",
                    placeholder="em branco: mantém o da placa" if atual.get("PIN_OPERADOR") else "")
                teclado = st.checkbox("Teclado 4x4 ligado (linhas nos GPIO 13, 14, 25, 33; colunas nos "
                                      "GPIO 21, 22, 15, 32)", value=bool(atual.get("TECLADO", False)))
                gravar = st.form_submit_button("Gravar na placa e reiniciar", type="primary")
            if gravar:
                valores = {"WIFI_SSID": ssid.strip(), "WIFI_SENHA": senha_wifi or atual.get("WIFI_SENHA", ""),
                           "API_URL": api_url.strip(), "TERMINAL_ID": terminal_id.strip(), "PINO_BOTAO": pino,
                           "PIN_OPERADOR": pin_operador or atual.get("PIN_OPERADOR", ""), "TECLADO": teclado}
                problemas = placa_usb.validar_config(valores)
                if problemas:
                    st.error("✕ Não gravado.  \n" + "  \n".join(problemas))
                else:
                    try:
                        with st.spinner("Gravando o config.py e reiniciando a placa..."):
                            placa.gravar_config(valores)
                        st.session_state["config_lido"] = valores
                        st.success("✓ config.py gravado e placa reiniciada. Veja o console abaixo para "
                                   "conferir a conexão.")
                    except placa_usb.ErroPlaca as erro:
                        st.error("✕ {}".format(erro))

            # 2. Enviar o código -------------------------------------------
            st.markdown("##### 2. Enviar o código")
            arquivos = placa_usb.arquivos_da_placa()
            st.caption("Copia os {} arquivos .py da pasta esp32/ para a placa (substitui os que estão lá; "
                       "o config.py não é mexido) e reinicia. Use quando o código mudar.".format(len(arquivos)))
            if st.button("Enviar {} arquivos e reiniciar".format(len(arquivos)), key="enviar_codigo"):
                try:
                    with st.spinner("Enviando... (leva cerca de 1 minuto)"):
                        enviados = placa.enviar_codigo(arquivos)
                    st.success("✓ {} arquivos enviados e placa reiniciada.".format(enviados))
                except placa_usb.ErroPlaca as erro:
                    st.error("✕ {}".format(erro))

            # 3. Console ---------------------------------------------------
            st.markdown("##### 3. Console da placa")
            reiniciar_col, escutar_col, _ = st.columns([1, 1, 3])
            reiniciar = reiniciar_col.button("Reiniciar e ver (15 s)", key="console_reiniciar")
            escutar = escutar_col.button("Só escutar (10 s)", key="console_escutar")
            if reiniciar or escutar:
                try:
                    with st.spinner("Lendo o console..."):
                        st.session_state["console"] = placa.console(15 if reiniciar else 10, reiniciar=reiniciar)
                except placa_usb.ErroPlaca as erro:
                    st.error("✕ {}".format(erro))
            if st.session_state.get("console") is not None:
                texto = st.session_state["console"]
                mostrar_verificacoes(placa_usb.resumo_do_console(texto))
                st.code(texto or "(a placa não escreveu nada nesse tempo)", language=None)
