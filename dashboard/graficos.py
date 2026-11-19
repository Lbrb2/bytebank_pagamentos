"""
graficos.py - Gráficos do dashboard (Altair, que já vem com o Streamlit)

Regras de visualização seguidas (e o porquê):

  * Cada gráfico tem UMA série e UMA cor: o azul da paleta (validado para
    daltonismo e contraste nos temas claro e escuro). O título diz o que está
    plotado, então não há legenda.
  * Cor só onde há dado. Texto (eixos, rótulos, valores) usa tons de cinza
    do tema, nunca a cor da série.
  * Barras finas (até 24 px), com ponta arredondada de 4 px e base reta,
    saindo de uma linha de base comum. Grade em linha fina e contínua.
  * Rótulos seletivos: só o maior valor do gráfico de tempo; nos rankings
    curtos (até 9 itens), o nome em cima de cada barra e o valor alinhado à
    direita, e aí o eixo some.
  * Passar o mouse mostra os valores (dica), e a área sensível é a coluna
    inteira, não só a barra. Cada gráfico tem também a tabela equivalente
    (no app), para quem não consegue ou não quer usar o mouse.
  * Um eixo só. Faturamento e número de vendas têm escalas diferentes: o
    número de vendas vai na dica e na tabela, não num segundo eixo.
"""

import altair as alt
import pandas as pd

from dados import reais

FONTE = 'system-ui, -apple-system, "Segoe UI", sans-serif'

# Paleta de referência (tons validados com o validador de paleta, nos dois temas)
TEMAS = {
    "light": {
        "serie": "#2a78d6", "serie_destaque": "#5598e7",
        "texto": "#0b0b0b", "texto_secundario": "#52514e", "texto_suave": "#898781",
        "grade": "#e1e0d9", "eixo": "#c3c2b7", "superficie": "#ffffff",
        # Rampa sequencial (uma cor, do claro ao escuro): pouco -> muito. Começa no
        # passo 250, e não no mais claro, para "1 venda" não sumir no fundo (validado)
        "sequencial": ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"],
        "celula_vazia": "#f0efec",
    },
    "dark": {
        "serie": "#3987e5", "serie_destaque": "#6da7ec",
        "texto": "#f0efec", "texto_secundario": "#c3c2b7", "texto_suave": "#898781",
        "grade": "#2c2c2a", "eixo": "#383835", "superficie": "#171614",
        # No escuro, "pouco" se aproxima do fundo e "muito" acende
        "sequencial": ["#184f95", "#256abf", "#3987e5", "#6da7ec", "#b7d3f6"],
        "celula_vazia": "#262624",
    },
}

# Situação (ok / atenção / problema): reservadas, sempre com ícone e texto
SITUACAO = {
    "bom": {"cor": "#0ca30c", "icone": "✓"},
    "atencao": {"cor": "#fab219", "icone": "!"},
    "critico": {"cor": "#d03b3b", "icone": "✕"},
}

LOCALE_BR = {
    "number": {"decimal": ",", "thousands": ".", "grouping": [3], "currency": ["R$ ", ""]},
}


def _configurar(grafico, tema):
    t = TEMAS[tema]
    return (grafico
            .configure(locale=LOCALE_BR, font=FONTE, background="transparent")
            .configure_view(stroke=None)
            .configure_axis(labelColor=t["texto_suave"], titleColor=t["texto_secundario"],
                            labelFontSize=12, gridColor=t["grade"], gridWidth=1,
                            domainColor=t["eixo"], tickColor=t["eixo"], tickSize=0, labelPadding=6)
            .configure_text(font=FONTE))


def faturamento_no_tempo(linhas, granularidade, tema="light", altura=240):
    """Colunas de faturamento por hora ou por dia."""
    t = TEMAS[tema]
    df = pd.DataFrame(linhas)
    df["faturamento_txt"] = df["faturamento_centavos"].map(reais)
    quantidade = len(df)
    largura = 24 if quantidade <= 12 else (14 if quantidade <= 24 else 8)
    titulo_x = "Hora" if granularidade == "hora" else "Dia"
    passe_o_mouse = alt.selection_point(on="pointerover", clear="pointerout", fields=["rotulo"], empty=False)

    base = alt.Chart(df).encode(
        x=alt.X("rotulo:N", sort=list(df["rotulo"]), title=None,
                axis=alt.Axis(labelAngle=0, labelOverlap="greedy", domain=True, grid=False)))
    barras = base.mark_bar(size=largura, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        y=alt.Y("faturamento:Q", title=None,
                axis=alt.Axis(format="$,.0f", tickCount=4, domain=False),
                scale=alt.Scale(nice=True)),
        color=alt.condition(passe_o_mouse, alt.value(t["serie_destaque"]), alt.value(t["serie"])),
    )
    # Área sensível: a coluna inteira (do chão ao topo), invisível
    alvo = base.mark_rect(opacity=0.001).encode(
        y=alt.value(0), y2=alt.value(altura),
        tooltip=[alt.Tooltip("rotulo:N", title=titulo_x),
                 alt.Tooltip("faturamento_txt:N", title="Faturamento"),
                 alt.Tooltip("vendas:Q", title="Vendas")],
    ).add_params(passe_o_mouse)
    camadas = [barras, alvo]
    maior = df["faturamento_centavos"].max()
    if maior > 0:
        # Rótulo seletivo: só o maior valor (mesma base, então o eixo x é o mesmo)
        rotulo = base.transform_filter(alt.datum.faturamento_centavos == int(maior)).mark_text(
            dy=-8, fontSize=12, color=t["texto_secundario"]).encode(y="faturamento:Q", text="faturamento_txt:N")
        camadas.insert(1, rotulo)
    return _configurar(alt.layer(*camadas).properties(height=altura), tema)


def ranking(linhas, campo_nome, campo_valor, titulo_valor, tema="light", texto_valor=None, extras=()):
    """Barras horizontais em ordem decrescente, com o NOME EM CIMA da barra.

    Com o nome ao lado, um rótulo comprido ("Cartão bloqueado por segurança")
    ocupava 2/3 da coluna e espremia as barras no terço restante. Com o nome
    em cima, cada barra usa a largura inteira, todas saem da mesma margem
    esquerda e o valor fica alinhado à direita, na mesma linha do nome.

    linhas: lista de dicts; campo_nome: rótulo; campo_valor: número.
    texto_valor: campo com o texto do valor (padrão: o próprio valor).
    extras: [(campo, título)] mostrados na dica.
    """
    t = TEMAS[tema]
    df = pd.DataFrame(linhas)
    texto_valor = texto_valor or campo_valor
    maximo = max(df[campo_valor].max(), 1)
    df["_zero"] = 0
    df["_maximo"] = maximo
    ordem = list(df.sort_values(campo_valor, ascending=False)[campo_nome])
    passe_o_mouse = alt.selection_point(on="pointerover", clear="pointerout", fields=[campo_nome], empty=False)
    dica = [alt.Tooltip(campo_nome + ":N", title="Item"), alt.Tooltip(campo_valor + ":Q", title=titulo_valor)]
    dica += [alt.Tooltip(campo + ":N", title=titulo) for campo, titulo in extras]

    altura_linha = 44
    base = alt.Chart(df).encode(
        y=alt.Y(campo_nome + ":N", sort=ordem, title=None, axis=None,
                scale=alt.Scale(paddingInner=0, paddingOuter=0)))
    escala_x = alt.Scale(domain=[0, maximo])
    barras = base.mark_bar(size=10, cornerRadiusEnd=4, yOffset=8).encode(
        x=alt.X(campo_valor + ":Q", axis=None, scale=escala_x),
        color=alt.condition(passe_o_mouse, alt.value(t["serie_destaque"]), alt.value(t["serie"])),
    )
    nomes = base.mark_text(align="left", baseline="bottom", dy=-1, fontSize=13,
                           color=t["texto_secundario"]).encode(
        x=alt.X("_zero:Q", scale=escala_x), text=campo_nome + ":N")
    valores = base.mark_text(align="right", baseline="bottom", dy=-1, fontSize=13,
                             color=t["texto_secundario"]).encode(
        x=alt.X("_maximo:Q", scale=escala_x),
        text=alt.Text(texto_valor + (":N" if texto_valor != campo_valor else ":Q")))
    # Área sensível: a linha inteira (nome, valor e barra)
    alvo = base.mark_bar(size=altura_linha - 2, opacity=0.001).encode(
        x=alt.X("_zero:Q", scale=escala_x), x2="_maximo", tooltip=dica).add_params(passe_o_mouse)
    grafico = alt.layer(barras, nomes, valores, alvo).properties(height=altura_linha * len(df))
    return _configurar(grafico, tema)


def linha_no_tempo(linhas, campo, titulo_valor, tema="light", formato_valor="{:.0f}", granularidade="5min",
                   altura=120, amplitude_minima=None):
    """Linha fina de uma série ao longo do tempo (saúde do terminal).

    Ponto e valor só no fim (o valor atual); o resto aparece passando o
    mouse, numa linha vertical que acha o horário mais próximo. A escala não
    começa no zero: o que interessa é a variação (sinal de -60 a -80 dBm,
    memória caindo aos poucos)."""
    t = TEMAS[tema]
    df = pd.DataFrame([l for l in linhas if l[campo] is not None])
    if df.empty:
        return None
    df["valor_txt"] = df[campo].map(lambda v: formato_valor.format(v).replace(".", ","))
    formato_x = "%H:%M" if granularidade == "5min" else "%d/%m %Hh"
    # Escala mínima: sem ela, uma variação de 0,3 KB (ruído) ocuparia o gráfico
    # inteiro e pareceria um problema
    menor, maior = float(df[campo].min()), float(df[campo].max())
    escala = alt.Scale(zero=False, nice=True)
    if amplitude_minima and maior - menor < amplitude_minima:
        meio = (menor + maior) / 2
        escala = alt.Scale(zero=False, nice=True,
                           domain=[meio - amplitude_minima / 2, meio + amplitude_minima / 2])
    perto = alt.selection_point(nearest=True, on="pointermove", fields=["momento"], empty=False,
                                clear="pointerout")
    base = alt.Chart(df).encode(x=alt.X("momento:T", title=None,
                                        axis=alt.Axis(format=formato_x, tickCount=4, grid=False, labelAngle=0)))
    linha = base.mark_line(strokeWidth=2, strokeCap="round", strokeJoin="round", color=t["serie"]).encode(
        y=alt.Y(campo + ":Q", title=None, scale=escala, axis=alt.Axis(tickCount=3, domain=False)))
    ultimo = df.tail(1)
    ponto_final = alt.Chart(ultimo).mark_circle(size=64, color=t["serie"], stroke=t["superficie"],
                                                strokeWidth=2, opacity=1).encode(x="momento:T", y=campo + ":Q")
    rotulo_final = alt.Chart(ultimo).mark_text(align="left", dx=8, fontSize=12,
                                               color=t["texto_secundario"]).encode(
        x="momento:T", y=campo + ":Q", text="valor_txt:N")
    regua = base.mark_rule(color=t["eixo"], strokeWidth=1).encode(
        opacity=alt.condition(perto, alt.value(1), alt.value(0)),
        tooltip=[alt.Tooltip("momento:T", title="Horário", format="%d/%m %H:%M"),
                 alt.Tooltip("valor_txt:N", title=titulo_valor)],
    ).add_params(perto)
    ponto_mouse = base.mark_circle(size=64, color=t["serie"], stroke=t["superficie"], strokeWidth=2).encode(
        y=campo + ":Q", opacity=alt.condition(perto, alt.value(1), alt.value(0)))
    grafico = alt.layer(linha, regua, ponto_mouse, ponto_final, rotulo_final).properties(
        height=altura, padding={"right": 48, "left": 4, "top": 6, "bottom": 4})
    return _configurar(grafico, tema)


def mapa_de_calor(linhas, tema="light"):
    """Vendas por dia da semana x hora, numa escala de uma cor só.

    Mais escuro (no tema claro) = mais vendas. Células sem venda ficam num
    cinza quase igual ao fundo. A legenda mostra a escala."""
    t = TEMAS[tema]
    df = pd.DataFrame(linhas)
    maximo = max(int(df["vendas"].max()), 1)
    horas = ["{:02d}h".format(h) for h in range(24)]
    dias = list(df.sort_values("ordem_dia")["dia"].drop_duplicates())
    base = alt.Chart(df).encode(
        x=alt.X("hora:O", sort=horas, title=None,
                scale=alt.Scale(paddingInner=0.12),
                axis=alt.Axis(labelAngle=0, domain=False, ticks=False,
                              values=["00h", "03h", "06h", "09h", "12h", "15h", "18h", "21h"])),
        y=alt.Y("dia:O", sort=dias, title=None, scale=alt.Scale(paddingInner=0.12),
                axis=alt.Axis(domain=False, ticks=False, labelColor=t["texto_secundario"])),
        tooltip=[alt.Tooltip("dia:N", title="Dia"), alt.Tooltip("hora:N", title="Hora"),
                 alt.Tooltip("vendas:Q", title="Vendas"), alt.Tooltip("faturamento_txt:N", title="Faturamento")],
    )
    vazias = base.mark_rect(cornerRadius=2, color=t["celula_vazia"])
    cheias = base.transform_filter(alt.datum.vendas > 0).mark_rect(cornerRadius=2).encode(
        color=alt.Color("vendas:Q", title="Vendas",
                        scale=alt.Scale(range=t["sequencial"], domain=[1, maximo]),
                        legend=alt.Legend(orient="right", gradientLength=120, gradientThickness=10,
                                          tickCount=3, format="d", labelColor=t["texto_suave"],
                                          titleColor=t["texto_secundario"], titleFontWeight="normal")))
    return _configurar(alt.layer(vazias, cheias).properties(height=28 * len(dias) + 10), tema)
