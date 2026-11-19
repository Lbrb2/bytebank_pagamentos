"""
dados.py - Conversa com a API do backend e prepara os números do dashboard

O dashboard NÃO lê o banco direto: tudo passa pela API (camada 2), como na
arquitetura do projeto. Assim, as regras de negócio ficam num lugar só, e o
dashboard funciona até em outro computador da rede.

As funções de cálculo (indicadores, agrupamento por hora) não dependem do
Streamlit, para poderem ser testadas sozinhas.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# Recife e o resto do Nordeste não têm horário de verão: UTC-3 o ano todo.
# (Um fuso fixo evita depender do pacote tzdata no Windows.)
FUSO_LOCAL = timezone(timedelta(hours=-3), "UTC-3")

PERIODOS = ("Hoje", "Últimas 24 h", "Últimos 7 dias", "Tudo")

ONLINE_ATE_S = 90   # sem contato há mais que isso: "sem contato"


class ErroAPI(Exception):
    def __init__(self, mensagem, codigo_http=None):
        super().__init__(mensagem)
        self.codigo_http = codigo_http       # 409 = o dado mudou enquanto o gerente editava


class ClienteAPI:
    def __init__(self, url_base, tempo_limite_s=4):
        self.url = url_base.rstrip("/")
        self._tempo_limite = tempo_limite_s

    def _requisitar(self, metodo, caminho, corpo=None, parametros=None):
        url = self.url + caminho
        if parametros:
            url += "?" + urllib.parse.urlencode({k: v for k, v in parametros.items() if v is not None})
        dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
        pedido = urllib.request.Request(url, data=dados, method=metodo,
                                        headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(pedido, timeout=self._tempo_limite) as resposta:
                return json.loads(resposta.read().decode("utf-8"))
        except urllib.error.HTTPError as erro:
            try:
                motivo = json.loads(erro.read().decode("utf-8")).get("motivo")
            except ValueError:
                motivo = None
            raise ErroAPI(motivo or "HTTP {}".format(erro.code), erro.code)
        except (urllib.error.URLError, OSError) as erro:
            raise ErroAPI("o backend não respondeu em {} ({})".format(self.url, getattr(erro, "reason", erro)))

    # Leitura
    def transacoes(self, desde=None, terminal=None, limite=5000, ate=None):
        return self._requisitar("GET", "/transacoes", parametros={
            "desde": desde, "ate": ate, "terminal": terminal, "limite": limite})["transacoes"]

    def telemetria(self, desde=None, terminal=None):
        return self._requisitar("GET", "/telemetria", parametros={
            "desde": desde, "terminal": terminal})["telemetria"]

    def mais_vendidos(self, desde=None, terminal=None, k=5):
        return self._requisitar("GET", "/relatorios/mais-vendidos", parametros={
            "desde": desde, "terminal": terminal, "k": k})["produtos"]

    def terminais(self):
        return self._requisitar("GET", "/terminais")["terminais"]

    def produtos(self):
        return self._requisitar("GET", "/produtos")["produtos"]

    def comandos(self, limite=8):
        return self._requisitar("GET", "/comandos", parametros={"limite": limite})["comandos"]

    def cartoes_bloqueados(self):
        return self._requisitar("GET", "/cartoes/bloqueados")["cartoes"]

    def desbloquear_cartao(self, uid):
        return self._requisitar("POST", "/cartoes/{}/desbloquear".format(urllib.parse.quote(uid)), {})

    def exportar(self, formato, desde=None, terminal=None):
        """Bytes do relatório (formato 'xlsx' ou 'csv') gerado pelo backend."""
        url = self.url + "/relatorios/exportar?" + urllib.parse.urlencode(
            {k: v for k, v in (("formato", formato), ("desde", desde), ("terminal", terminal)) if v})
        try:
            with urllib.request.urlopen(url, timeout=30) as resposta:
                return resposta.read()
        except (urllib.error.URLError, OSError) as erro:
            raise ErroAPI("o relatório não foi gerado ({})".format(getattr(erro, "reason", erro)))

    def auditoria(self, limite=30):
        return self._requisitar("GET", "/auditoria", parametros={"limite": limite})["auditoria"]

    # Ações do gerente
    def enviar_comando(self, terminal, tipo, **parametros):
        corpo = dict(parametros, tipo=tipo)
        return self._requisitar("POST", "/terminais/{}/comandos".format(urllib.parse.quote(terminal)), corpo)

    def alterar_produto(self, codigo, preco_centavos=None, estoque=None, estoque_esperado=None):
        """Muda o preço e/ou corrige a contagem. Com estoque_esperado (o valor que
        o gerente viu), o backend recusa com 409 se uma venda mudou o estoque."""
        corpo = {k: v for k, v in (("preco_centavos", preco_centavos), ("estoque", estoque),
                                   ("estoque_esperado", estoque_esperado)) if v is not None}
        return self._requisitar("PATCH", "/produtos/{}".format(codigo), corpo)

    def repor_estoque(self, codigo, unidades):
        """Chegou mercadoria: soma as unidades ao estoque atual."""
        return self._requisitar("POST", "/produtos/{}/reposicao".format(codigo), {"unidades": unidades})


# ---------------------------------------------------------------- tempo

def agora_local():
    return datetime.now(FUSO_LOCAL)


def para_local(texto_utc):
    """'2026-11-19T17:03:10Z' -> datetime no horário local."""
    return datetime.strptime(texto_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(FUSO_LOCAL)


def para_utc_iso(momento):
    return momento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def inicio_do_periodo(periodo, agora=None):
    """Início do período no horário local (None = desde sempre)."""
    agora = agora or agora_local()
    if periodo == "Hoje":
        return agora.replace(hour=0, minute=0, second=0, microsecond=0)
    if periodo == "Últimas 24 h":
        return agora - timedelta(hours=24)
    if periodo == "Últimos 7 dias":
        return (agora - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    return None


# ---------------------------------------------------------------- dinheiro

def reais(centavos, compacto=False):
    valor = centavos / 100
    if compacto and abs(valor) >= 10000:
        return "R$ {:,.1f} mil".format(valor / 1000).replace(",", "X").replace(".", ",").replace("X", ".")
    texto = "{:,.2f}".format(valor)
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


# ---------------------------------------------------------------- indicadores

def indicadores(transacoes):
    """Números do topo do dashboard a partir da lista de transações do período."""
    aprovadas = [t for t in transacoes if t["status"] == "aprovada"]
    recusadas = [t for t in transacoes if t["status"] == "recusada"]
    estornadas = [t for t in transacoes if t["status"] == "estornada"]
    faturamento = sum(t["total_centavos"] for t in aprovadas)
    tentativas = len(transacoes)
    return {
        "faturamento_centavos": faturamento,
        "vendas": len(aprovadas),
        "ticket_medio_centavos": faturamento // len(aprovadas) if aprovadas else 0,
        "recusadas": len(recusadas),
        "taxa_recusa": len(recusadas) / tentativas if tentativas else 0.0,
        "estornos": len(estornadas),
        "estornado_centavos": sum(t["total_centavos"] for t in estornadas),
        "offline": sum(1 for t in aprovadas if t["origem"] == "contingencia"),
    }


def faturamento_no_tempo(transacoes, periodo, agora=None):
    """Faturamento das vendas aprovadas em intervalos de 1 hora (Hoje, 24 h)
    ou de 1 dia (7 dias, Tudo). Intervalos sem venda aparecem com zero: um
    buraco no gráfico também é informação."""
    agora = agora or agora_local()
    por_hora = periodo in ("Hoje", "Últimas 24 h")
    passo = timedelta(hours=1) if por_hora else timedelta(days=1)

    def arredondar(momento):
        if por_hora:
            return momento.replace(minute=0, second=0, microsecond=0)
        return momento.replace(hour=0, minute=0, second=0, microsecond=0)

    aprovadas = [(para_local(t["recebida_em"]), t["total_centavos"]) for t in transacoes
                 if t["status"] == "aprovada"]
    inicio = inicio_do_periodo(periodo, agora)
    if inicio is None:
        inicio = min((m for m, _ in aprovadas), default=agora)
    intervalo = arredondar(inicio)
    fim = arredondar(agora)

    grupos = {}
    while intervalo <= fim:
        grupos[intervalo] = {"vendas": 0, "faturamento_centavos": 0}
        intervalo += passo
    for momento, total in aprovadas:
        chave = arredondar(momento)
        if chave in grupos:
            grupos[chave]["vendas"] += 1
            grupos[chave]["faturamento_centavos"] += total

    linhas = []
    for momento, valores in grupos.items():
        rotulo = momento.strftime("%Hh") if por_hora else momento.strftime("%d/%m")
        linhas.append({"inicio": momento, "rotulo": rotulo, "vendas": valores["vendas"],
                       "faturamento_centavos": valores["faturamento_centavos"],
                       "faturamento": valores["faturamento_centavos"] / 100})
    return linhas, ("hora" if por_hora else "dia")


def recusas_por_motivo(transacoes):
    contagem = {}
    for t in transacoes:
        if t["status"] == "recusada":
            motivo = t["motivo"] or "Sem motivo"
            contagem[motivo] = contagem.get(motivo, 0) + 1
    return sorted(({"motivo": m, "quantidade": q} for m, q in contagem.items()),
                  key=lambda linha: (-linha["quantidade"], linha["motivo"]))


def alertas_de_fraude(cartoes_bloqueados):
    """Só os bloqueios automáticos por suspeita (os bloqueios do banco não são alertas)."""
    return [c for c in cartoes_bloqueados if (c.get("motivo_bloqueio") or "").startswith("Suspeita")]


def situacao_do_terminal(terminal, agora=None):
    """('bom' | 'atencao' | 'critico', rótulo) para mostrar com ícone e texto."""
    agora = agora or agora_local()
    if terminal["status"] == "bloqueado":
        return "critico", "Bloqueado"
    if not terminal["ultimo_contato"]:
        return "atencao", "Nunca se conectou"
    segundos = (agora - para_local(terminal["ultimo_contato"])).total_seconds()
    if segundos <= ONLINE_ATE_S:
        return "bom", "Online"
    if segundos < 3600:
        return "atencao", "Sem contato há {} min".format(int(segundos // 60))
    return "atencao", "Sem contato desde {}".format(para_local(terminal["ultimo_contato"]).strftime("%d/%m %H:%M"))


# ================================================================ comparação com o período anterior

DIAS = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")


def periodo_anterior(periodo, agora=None):
    """(início, fim, rótulo) do período de comparação, ou None em "Tudo".

    "Hoje" compara com ONTEM ATÉ A MESMA HORA: às 10h, comparar com o dia
    inteiro de ontem faria hoje parecer sempre pior."""
    agora = agora or agora_local()
    if periodo == "Hoje":
        inicio = inicio_do_periodo("Hoje", agora) - timedelta(days=1)
        return inicio, agora - timedelta(days=1), "ontem até {}".format(agora.strftime("%Hh%M"))
    if periodo == "Últimas 24 h":
        return agora - timedelta(hours=48), agora - timedelta(hours=24), "as 24 h anteriores"
    if periodo == "Últimos 7 dias":
        inicio = inicio_do_periodo(periodo, agora)
        return inicio - timedelta(days=7), inicio, "os 7 dias anteriores"
    return None


def variacao(atual, anterior, para_cima_e_bom=True, em_porcentagem=True):
    """Texto e tom da variação para o número do topo.

    Devolve (texto, tom), com tom 'bom', 'ruim' ou 'neutro'. A direção vai na
    seta e no sinal, não só na cor."""
    diferenca = atual - anterior
    if diferenca == 0:
        return "= igual", "neutro"
    seta = "▲" if diferenca > 0 else "▼"
    if em_porcentagem:
        if anterior == 0:
            return "{} (antes: nenhuma)".format(seta), ("bom" if (diferenca > 0) == para_cima_e_bom else "ruim")
        if atual >= anterior * 10:
            texto = "{} {:.0f}x".format(seta, atual / anterior)      # "▲ 2300%" é difícil de ler
        else:
            texto = "{} {:.0f}%".format(seta, abs(diferenca) / anterior * 100)
    else:
        texto = "{} {}".format(seta, abs(diferenca))
    return texto, ("bom" if (diferenca > 0) == para_cima_e_bom else "ruim")


# ================================================================ estoque

def previsao_de_estoque(produtos, vendidos_ultima_hora):
    """Para cada produto: estoque, ritmo de venda (unidades na última hora)
    e quanto tempo dura nesse ritmo. Ordenado do mais urgente ao menos."""
    ritmo = {p["codigo"]: p["unidades"] for p in vendidos_ultima_hora}
    linhas = []
    for produto in produtos:
        por_hora = ritmo.get(produto["codigo"], 0)
        estoque = produto["estoque"]
        horas = estoque / por_hora if por_hora else None
        if estoque == 0:
            nivel, previsao = "critico", "Esgotado"
        elif horas is not None and horas < 1:
            nivel, previsao = "critico", "Acaba em ~{}".format(duracao(horas * 3600, curta=True))
        elif (horas is not None and horas < 3) or estoque <= 5:
            nivel = "atencao"
            previsao = ("Acaba em ~{}".format(duracao(horas * 3600, curta=True)) if horas is not None
                        else "Estoque baixo")
        else:
            nivel = "bom"
            previsao = ("Dura ~{}".format(duracao(horas * 3600, curta=True)) if horas is not None
                        else "Sem vendas há 1 h")
        linhas.append({"codigo": produto["codigo"], "nome": produto["nome"], "estoque": estoque,
                       "por_hora": por_hora, "horas": horas, "nivel": nivel, "previsao": previsao})
    ordem = {"critico": 0, "atencao": 1, "bom": 2}
    return sorted(linhas, key=lambda l: (ordem[l["nivel"]], l["horas"] if l["horas"] is not None else 1e9,
                                         l["estoque"]))


def duracao(segundos, curta=False):
    """3725 -> '1 h 02 min' | 200 -> '3 min 20 s' | 40 -> '40 s' (curta: sem segundos acima de 1 min)."""
    segundos = int(round(segundos))
    if segundos >= 86400:
        return "{} dias".format(segundos // 86400) if segundos >= 172800 else "1 dia"
    horas, resto = divmod(segundos, 3600)
    minutos, segundos = divmod(resto, 60)
    if horas:
        return "{} h {:02d} min".format(horas, minutos)
    if minutos:
        return "{} min".format(minutos) if curta else "{} min {:02d} s".format(minutos, segundos)
    return "{} s".format(segundos)


# ================================================================ saúde dos terminais

def serie_de_telemetria(registros, periodo):
    """Agrupa a telemetria (1 pacote por minuto) em intervalos, com a média
    de cada um: 5 min para hoje e 24 h, 1 h para 7 dias e tudo. Cada ponto
    do gráfico continua legível e a curva não vira um borrão."""
    passo = 5 * 60 if periodo in ("Hoje", "Últimas 24 h") else 3600
    grupos = {}
    for registro in registros:
        momento = para_local(registro["registrada_em"])
        segundo = int(momento.timestamp()) // passo * passo
        chave = (registro["terminal"], segundo)
        grupo = grupos.setdefault(chave, {"rssi": [], "memoria": [], "fila": []})
        if registro["rssi"] is not None:
            grupo["rssi"].append(registro["rssi"])
        if registro["memoria_livre"] is not None:
            grupo["memoria"].append(registro["memoria_livre"] / 1024)
        if registro["fila_pendente"] is not None:
            grupo["fila"].append(registro["fila_pendente"])
    linhas = []
    for (terminal, segundo), grupo in sorted(grupos.items()):
        linhas.append({
            "terminal": terminal,
            "momento": datetime.fromtimestamp(segundo, FUSO_LOCAL).replace(tzinfo=None),
            "rssi": round(sum(grupo["rssi"]) / len(grupo["rssi"]), 1) if grupo["rssi"] else None,
            "memoria_kb": round(sum(grupo["memoria"]) / len(grupo["memoria"]), 1) if grupo["memoria"] else None,
            "fila": max(grupo["fila"]) if grupo["fila"] else None,
        })
    return linhas


def tendencia_de_memoria(linhas):
    """Aviso quando a memória livre só cai (sinal de vazamento no código da placa)."""
    valores = [l["memoria_kb"] for l in linhas if l["memoria_kb"] is not None]
    if len(valores) < 6:
        return None
    terco = len(valores) // 3
    inicio = sum(valores[:terco]) / terco      # média do primeiro terço do período
    fim = sum(valores[-3:]) / 3                # agora (média dos 3 últimos pontos)
    if fim < inicio * 0.9:
        return "Memória livre caiu {:.0f}% no período: pode ser vazamento de memória.".format(
            (1 - fim / inicio) * 100)
    return None


# ================================================================ horário de pico

def mapa_de_calor(transacoes):
    """Vendas aprovadas por dia da semana x hora (todas as 7 x 24 células)."""
    celulas = {(dia, hora): {"vendas": 0, "faturamento_centavos": 0} for dia in range(7) for hora in range(24)}
    for t in transacoes:
        if t["status"] != "aprovada":
            continue
        momento = para_local(t["recebida_em"])
        celula = celulas[(momento.weekday(), momento.hour)]
        celula["vendas"] += 1
        celula["faturamento_centavos"] += t["total_centavos"]
    return [{"dia": DIAS[dia], "ordem_dia": dia, "hora": "{:02d}h".format(hora), "vendas": c["vendas"],
             "faturamento_txt": reais(c["faturamento_centavos"])}
            for (dia, hora), c in sorted(celulas.items())]


# ================================================================ fila offline

def tempos_da_fila_offline(transacoes):
    """Quanto as vendas offline esperaram até chegar ao servidor: hora em que
    chegou menos a hora da venda no terminal. Vendas feitas com o relógio da
    placa ainda sem acertar (sem NTP) ficam de fora da conta."""
    offline = [t for t in transacoes if t["origem"] == "contingencia"]
    medidas, sem_relogio = [], 0
    for t in offline:
        if not t.get("relogio_sincronizado") or not t.get("criada_no_terminal"):
            sem_relogio += 1
            continue
        espera = (para_local(t["recebida_em"]) - para_local(t["criada_no_terminal"])).total_seconds()
        medidas.append({"venda": para_local(t["criada_no_terminal"]), "chegada": para_local(t["recebida_em"]),
                        "espera_s": max(0, espera), "terminal": t["terminal"], "status": t["status"],
                        "total_centavos": t["total_centavos"]})
    esperas = sorted(m["espera_s"] for m in medidas)
    mediana = None
    if esperas:
        meio = len(esperas) // 2
        mediana = esperas[meio] if len(esperas) % 2 else (esperas[meio - 1] + esperas[meio]) / 2
    return {"quantidade": len(offline), "medidas": medidas, "sem_relogio": sem_relogio,
            "mediana_s": mediana, "maximo_s": esperas[-1] if esperas else None}
