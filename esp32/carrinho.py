"""
carrinho.py - Itens da venda em andamento (MicroPython)

Duas estruturas trabalham juntas:

  _quantidades  dicionário (tabela hash) código -> quantidade
                somar mais uma unidade de um produto é O(1)

  _inclusoes    PILHA (LIFO) com o código de cada unidade, na ordem em que
                foi incluída. A tecla '*' desfaz a ÚLTIMA inclusão: é um
                pop() no topo da pilha, O(1). É o mesmo princípio do Ctrl+Z.
"""

MAXIMO_DE_UNIDADES = 30   # protege a memória da placa e evita erros de digitação


class Carrinho:
    def __init__(self):
        self._quantidades = {}
        self._inclusoes = []

    def adicionar(self, codigo):
        """Inclui uma unidade do produto. Devolve False se o carrinho encheu."""
        if len(self._inclusoes) >= MAXIMO_DE_UNIDADES:
            return False
        self._quantidades[codigo] = self._quantidades.get(codigo, 0) + 1
        self._inclusoes.append(codigo)
        return True

    def remover_ultimo(self):
        """Desfaz a última inclusão. Devolve o código removido, ou None."""
        if not self._inclusoes:
            return None
        codigo = self._inclusoes.pop()
        restante = self._quantidades[codigo] - 1
        if restante:
            self._quantidades[codigo] = restante
        else:
            del self._quantidades[codigo]
        return codigo

    def limpar(self):
        self._quantidades = {}
        self._inclusoes = []

    def vazio(self):
        return not self._inclusoes

    def unidades(self):
        return len(self._inclusoes)

    def quantidade(self, codigo):
        return self._quantidades.get(codigo, 0)

    def itens(self):
        """Lista de {codigo, quantidade} na ordem em que cada produto entrou.

        É o que vai para o backend. O preço NÃO vai: quem calcula o total
        cobrado é o servidor, com os preços do banco de dados.
        """
        vistos = {}
        resultado = []
        for codigo in self._inclusoes:
            if codigo not in vistos:
                vistos[codigo] = True
                resultado.append({"codigo": codigo, "quantidade": self._quantidades[codigo]})
        return resultado

    def total_centavos(self, catalogo):
        """Total pelos preços do catálogo local. Serve para mostrar na tela;
        o valor cobrado de fato é o que o servidor calcular."""
        total = 0
        for codigo, quantidade in self._quantidades.items():
            produto = catalogo.buscar(codigo)
            if produto is not None:
                total += produto["preco_centavos"] * quantidade
        return total
