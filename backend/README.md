# Backend — API em Python (Entrega 3 · 12/11)

API em FastAPI que recebe as requisições do terminal, valida os pacotes JSON,
localiza cartões e produtos em tabelas hash (O(1)) e grava as vendas no banco
de forma atômica.

Endpoints previstos:

| Método | Rota | Uso |
|---|---|---|
| GET | `/produtos` | catálogo para o terminal |
| POST | `/transacoes` | venda (uid do cartão + itens) |
| POST | `/telemetria` | estado do terminal (sinal, fila pendente) |
| GET | `/terminais/{codigo}/comandos` | comandos pendentes para o terminal |
