"""
catalogo.py - Produtos vendidos no terminal (MicroPython)

Os produtos ficam numa TABELA HASH (dicionário) código -> produto. Quando o
cliente aperta uma tecla, o produto é encontrado em O(1), sem percorrer a
lista inteira.

De onde vem o catálogo, em ordem de preferência:
  1. do servidor (GET /produtos), quando há conexão;
  2. da cópia gravada na flash da última vez que o servidor respondeu;
  3. do catálogo padrão abaixo, para o terminal funcionar mesmo no
     primeiro uso sem rede.

Cada produto tem um código de UM dígito, de 1 a 9: é a tecla que o cliente
aperta. Por isso o terminal mostra no máximo 9 produtos.

Estoque: o servidor manda quantas unidades restam de cada produto. O terminal
usa esse número para mostrar "esgot" e não deixar incluir mais do que há.
É só uma cópia: quem decide de verdade é o servidor, que confere o estoque
de novo em cada venda. Um produto sem estoque informado (como os do
catálogo padrão) não tem limite no terminal.
"""

import arquivos

ARQUIVO = "catalogo.json"
CODIGOS_VALIDOS = ("1", "2", "3", "4", "5", "6", "7", "8", "9")

PADRAO = [
    {"codigo": "1", "nome": "Cafe", "preco_centavos": 500},
    {"codigo": "2", "nome": "Agua", "preco_centavos": 300},
    {"codigo": "3", "nome": "Suco", "preco_centavos": 750},
    {"codigo": "4", "nome": "Pao de queijo", "preco_centavos": 450},
    {"codigo": "5", "nome": "Bolo", "preco_centavos": 600},
]


def _estoque(produto):
    """Estoque informado (inteiro >= 0) ou None se não houver controle."""
    estoque = produto.get("estoque")
    if isinstance(estoque, int) and not isinstance(estoque, bool) and estoque >= 0:
        return estoque
    return None


def _produto_valido(produto):
    try:
        return (str(produto["codigo"]) in CODIGOS_VALIDOS
                and isinstance(produto["nome"], str) and produto["nome"].strip() != ""
                and isinstance(produto["preco_centavos"], int)
                and produto["preco_centavos"] > 0)
    except (KeyError, TypeError):
        return False


class Catalogo:
    def __init__(self, arquivo=ARQUIVO):
        self._arquivo = arquivo
        self._produtos = {}   # tabela hash: código -> produto
        self._ordem = []      # códigos em ordem crescente, para exibir
        self.origem = "nenhuma"

    # ---------------- Carga ----------------

    def carregar_lista(self, lista, origem):
        """Monta o catálogo a partir de uma lista de produtos.

        Produtos inválidos (código fora de 1-9, preço não inteiro...) são
        ignorados um a um, para que um item errado não derrube o catálogo
        inteiro. Devolve quantos produtos foram aceitos.
        """
        produtos = {}
        for produto in lista:
            if _produto_valido(produto):
                codigo = str(produto["codigo"])
                produtos[codigo] = {
                    "codigo": codigo,
                    "nome": produto["nome"].strip(),
                    "preco_centavos": produto["preco_centavos"],
                    "estoque": _estoque(produto),
                }
        if not produtos:
            return 0
        self._produtos = produtos
        self._ordem = sorted(produtos)
        self.origem = origem
        return len(produtos)

    def iniciar(self, api=None):
        """Carrega o catálogo: servidor, depois flash, depois o padrão."""
        if api is not None:
            lista = api.obter_catalogo()
            if lista is not None and self.carregar_lista(lista, "servidor"):
                self.salvar()
                print("[CATALOGO] {} produto(s) recebidos do servidor.".format(len(self)))
                return self.origem
            print("[CATALOGO] Servidor não enviou o catálogo. Tentando a cópia local.")

        lista = self._ler_arquivo()
        if lista is not None and self.carregar_lista(lista, "flash"):
            print("[CATALOGO] {} produto(s) carregados da cópia local.".format(len(self)))
            return self.origem

        self.carregar_lista(PADRAO, "padrao")
        print("[CATALOGO] Usando o catálogo padrão ({} produtos).".format(len(self)))
        return self.origem

    def atualizar(self, api):
        """Busca o catálogo no servidor de novo (comando remoto ou recarga
        periódica). Sem resposta, mantém o catálogo atual. Devolve True se
        o catálogo foi trocado."""
        lista = api.obter_catalogo()
        if lista is None or not self.carregar_lista(lista, "servidor"):
            return False
        self.salvar()
        print("[CATALOGO] Atualizado: {} produto(s).".format(len(self)))
        return True

    def salvar(self):
        """Grava a cópia local na flash, de forma segura (ver arquivos.py)."""
        return arquivos.gravar_json(self._arquivo, self.produtos())

    def _ler_arquivo(self):
        dados, situacao = arquivos.ler_json(self._arquivo)
        if situacao == arquivos.CORROMPIDO:
            print("[CATALOGO] Cópia local corrompida; será ignorada.")
        return dados if isinstance(dados, list) else None

    # ---------------- Consulta ----------------

    def buscar(self, codigo):
        """Produto pelo código da tecla, em O(1). None se não existir."""
        return self._produtos.get(codigo)

    def estoque(self, codigo):
        """Unidades disponíveis, ou None se o produto não tem controle de estoque."""
        produto = self._produtos.get(codigo)
        return None if produto is None else produto.get("estoque")

    def baixar_estoque(self, itens):
        """Desconta os itens vendidos da cópia local do estoque."""
        self._ajustar_estoque(itens, -1)

    def devolver_estoque(self, itens):
        """Devolve os itens de uma venda cancelada."""
        self._ajustar_estoque(itens, +1)

    def _ajustar_estoque(self, itens, sinal):
        mudou = False
        for item in itens:
            produto = self._produtos.get(item["codigo"])
            if produto is not None and produto.get("estoque") is not None:
                produto["estoque"] = max(0, produto["estoque"] + sinal * item["quantidade"])
                mudou = True
        if mudou:
            self.salvar()   # para o "esgot" continuar certo depois de reiniciar

    def produtos(self):
        return [self._produtos[codigo] for codigo in self._ordem]

    def __len__(self):
        return len(self._produtos)

