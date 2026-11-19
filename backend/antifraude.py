"""
antifraude.py - Detecção de fraude em tempo real (extra)

Cada compra ONLINE passa por duas regras antes de debitar o saldo. Se
uma delas dispara, o cartão é bloqueado na hora (bloqueio preventivo), a
compra é recusada com "Cartão bloqueado por segurança" e o caso vai para
a auditoria e para os alertas do painel. O gerente desbloqueia pelo painel.

REGRA 1 - muitas compras em pouco tempo (cartão clonado sendo testado)

    Mais de MAX_COMPRAS_NA_JANELA tentativas em JANELA_S segundos.

    Cada cartão tem uma FILA de janela deslizante (deque) com os instantes
    das últimas tentativas:

        janela do cartão 532F07FE:  [ 10.0 | 31.5 | 52.2 ]  <- nova: 64.0
                                       ^
        64.0 - 10.0 > 60 s: sai pela frente (popleft, O(1)); a nova entra
        pelo fim (append, O(1)). Ficam 3 na janela: ainda não é fraude.
        Se ficassem 4, seria.

    Cada tentativa custa O(1) em média: cada instante entra e sai da fila
    uma única vez.

REGRA 2 - valor muito acima do normal para aquele cliente

    Valor >= VALOR_MINIMO_CENTAVOS e maior que MULTIPLICADOR x a média das
    últimas HISTORICO compras aprovadas do cartão (só depois de pelo menos
    MIN_HISTORICO compras, para conhecer o cliente).

    As últimas compras ficam numa fila de tamanho fixo (deque com maxlen):
    a mais antiga sai sozinha quando entra uma nova. A soma é mantida junto,
    então a média sai em O(1), sem somar a fila toda de novo.

Cartão -> fila: tabelas hash (backend/tabela_hash.py), busca O(1).

Vendas da FILA OFFLINE do terminal (origem "contingencia") não passam
pelas regras: elas chegam em sequência quando a rede volta (pareceriam
"muitas compras em pouco tempo"), já aconteceram no balcão e já são
limitadas a R$ 50,00 pelo próprio terminal.
"""

import time
from collections import deque

from .tabela_hash import TabelaHash

JANELA_S = 60                   # regra 1: janela de tempo
MAX_COMPRAS_NA_JANELA = 3       # regra 1: a 4ª tentativa em 60 s bloqueia
HISTORICO = 10                  # regra 2: média das últimas 10 compras
MIN_HISTORICO = 3               # regra 2: só avalia depois de 3 compras
MULTIPLICADOR = 5               # regra 2: 5x acima da média
VALOR_MINIMO_CENTAVOS = 3000    # regra 2: abaixo de R$ 30,00 nunca é suspeito


def _reais(centavos):
    texto = "{:,.2f}".format(centavos / 100)
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


class Antifraude:
    def __init__(self, relogio=time.monotonic):
        self._relogio = relogio
        self._tentativas = TabelaHash()    # uid -> deque com os instantes das tentativas
        self._compras = TabelaHash()       # uid -> [deque(maxlen=HISTORICO) de valores, soma]

    def avaliar(self, uid, total_centavos, origem="online"):
        """Registra a tentativa e devolve o motivo da suspeita, ou None."""
        if origem == "contingencia":
            return None
        agora = self._relogio()

        # Regra 1: janela deslizante
        janela = self._tentativas.buscar(uid)
        if janela is None:
            janela = deque()
            self._tentativas.inserir(uid, janela)
        while janela and agora - janela[0] > JANELA_S:
            janela.popleft()
        janela.append(agora)
        if len(janela) > MAX_COMPRAS_NA_JANELA:
            return "{} compras em menos de {} s".format(len(janela), JANELA_S)

        # Regra 2: valor fora do padrão do cliente
        historico = self._compras.buscar(uid)
        if historico is not None and len(historico[0]) >= MIN_HISTORICO:
            media = historico[1] / len(historico[0])
            if total_centavos >= VALOR_MINIMO_CENTAVOS and total_centavos > MULTIPLICADOR * media:
                return "valor de {} acima de {}x a média ({})".format(
                    _reais(total_centavos), MULTIPLICADOR, _reais(int(media)))
        return None

    def registrar_aprovada(self, uid, total_centavos):
        """Guarda o valor de uma compra aprovada para a média (regra 2)."""
        historico = self._compras.buscar(uid)
        if historico is None:
            historico = [deque(maxlen=HISTORICO), 0]
            self._compras.inserir(uid, historico)
        valores = historico[0]
        if len(valores) == valores.maxlen:
            historico[1] -= valores[0]          # o mais antigo vai sair da fila
        valores.append(total_centavos)
        historico[1] += total_centavos

    def esquecer_tentativas(self, uid):
        """Chamado ao desbloquear: o cartão recomeça com a janela vazia."""
        self._tentativas.remover(uid)

    def carregar_historico(self, conexao):
        """Refaz as médias a partir do banco quando o servidor liga."""
        linhas = conexao.execute(
            "SELECT c.uid_rfid, t.total_centavos FROM transacoes t JOIN cartoes c ON c.id = t.cartao_id "
            "WHERE t.status = 'aprovada' AND t.origem = 'online' ORDER BY t.id").fetchall()
        for linha in linhas:
            self.registrar_aprovada(linha["uid_rfid"], linha["total_centavos"])
