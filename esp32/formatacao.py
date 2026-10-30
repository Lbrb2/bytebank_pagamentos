"""
formatacao.py - Textos para o display e valores em dinheiro (MicroPython)

O OLED de 128x64 pixels, com a fonte padrão de 8x8, mostra 8 linhas de 16
caracteres. A fonte só tem os caracteres ASCII: "Café" apareceria com um
símbolo estranho no lugar do "é". Por isso todo texto passa por para_oled().

Valores em dinheiro são sempre inteiros em CENTAVOS (1300 = R$ 13,00). Somar
números com casas decimais acumula erros de arredondamento (0.1 + 0.2 dá
0.30000000000000004); somar inteiros, não.
"""

LARGURA = 16    # caracteres por linha
ALTURA = 8      # linhas na tela

_SEM_ACENTO = {
    "á": "a", "à": "a", "â": "a", "ã": "a", "ä": "a",
    "é": "e", "è": "e", "ê": "e", "ë": "e",
    "í": "i", "ì": "i", "î": "i", "ï": "i",
    "ó": "o", "ò": "o", "ô": "o", "õ": "o", "ö": "o",
    "ú": "u", "ù": "u", "û": "u", "ü": "u",
    "ç": "c", "ñ": "n",
    "Á": "A", "À": "A", "Â": "A", "Ã": "A", "Ä": "A",
    "É": "E", "È": "E", "Ê": "E", "Ë": "E",
    "Í": "I", "Ì": "I", "Î": "I", "Ï": "I",
    "Ó": "O", "Ò": "O", "Ô": "O", "Õ": "O", "Ö": "O",
    "Ú": "U", "Ù": "U", "Û": "U", "Ü": "U",
    "Ç": "C", "Ñ": "N",
}


def para_oled(texto):
    """Troca acentos por letras simples e qualquer outro caractere fora do
    ASCII por '?', para que tudo apareça na fonte do display."""
    saida = []
    for caractere in str(texto):
        if caractere in _SEM_ACENTO:
            saida.append(_SEM_ACENTO[caractere])
        elif ord(caractere) < 128:
            saida.append(caractere)
        else:
            saida.append("?")
    return "".join(saida)


def linha(texto):
    """Texto pronto para uma linha do display: sem acentos e com no máximo
    16 caracteres."""
    return para_oled(texto)[:LARGURA]


def centralizar(texto):
    texto = linha(texto)
    margem = (LARGURA - len(texto)) // 2
    return " " * margem + texto


def esquerda_direita(esquerda, direita):
    """Junta dois textos na mesma linha: um encostado à esquerda e o outro à
    direita. Se não couberem, a parte da esquerda é cortada."""
    direita = para_oled(direita)
    espaco = LARGURA - len(direita) - 1
    esquerda = para_oled(esquerda)[:max(espaco, 0)]
    return linha(esquerda + " " * (LARGURA - len(esquerda) - len(direita)) + direita)


def quebrar(texto, largura=LARGURA):
    """Divide um texto em linhas de até `largura` caracteres, sem cortar
    palavras no meio (a não ser que a palavra sozinha não caiba)."""
    linhas = []
    atual = ""
    for palavra in para_oled(texto).split():
        while len(palavra) > largura:
            if atual:
                linhas.append(atual)
                atual = ""
            linhas.append(palavra[:largura])
            palavra = palavra[largura:]
        if not atual:
            atual = palavra
        elif len(atual) + 1 + len(palavra) <= largura:
            atual = atual + " " + palavra
        else:
            linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas


def _milhares(numero):
    texto = str(numero)
    partes = []
    while len(texto) > 3:
        partes.insert(0, texto[-3:])
        texto = texto[:-3]
    partes.insert(0, texto)
    return ".".join(partes)


def valor(centavos):
    """1300 -> '13,00' | 125050 -> '1.250,50' | -500 -> '-5,00'"""
    sinal = "-" if centavos < 0 else ""
    inteiro, resto = divmod(abs(int(centavos)), 100)
    return "{}{},{:02d}".format(sinal, _milhares(inteiro), resto)


def reais(centavos):
    """1300 -> 'R$ 13,00' | -500 -> '-R$ 5,00'"""
    if centavos < 0:
        return "-R$ " + valor(-centavos)
    return "R$ " + valor(centavos)
