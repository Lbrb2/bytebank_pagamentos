# Backend — API em FastAPI (Entrega 3)

Recebe as requisições do terminal, valida os pacotes JSON, localiza cartões,
produtos e vendas em **tabelas hash O(1)** e grava tudo no SQLite com
**transações atômicas**.

```bash
pip install -r backend/requirements.txt
python -m backend                # da pasta raiz do repositório
```

| Arquivo | O que faz |
|---|---|
| `__main__.py` | inicia o servidor e mostra o `API_URL` para o ESP32 |
| `app.py` | rotas e tratamento de pacotes inválidos (HTTP 400 com motivo) |
| `modelos.py` | formato de cada pacote (Pydantic) |
| `servicos.py` | regras de negócio: venda, estorno, resumo, comandos, telemetria |
| `antifraude.py` | detecção de fraude: janela deslizante por cartão e média das últimas compras |
| `relatorios.py` | relatório para baixar: Excel (5 abas) e CSV |
| `tabela_hash.py` | tabela hash implementada do zero (FNV-1a, encadeamento separado) |
| `banco.py` | conexão com o SQLite e `transacao()`: BEGIN IMMEDIATE / COMMIT / ROLLBACK |
| `tests/` | 85 testes, incluindo o ponta a ponta, a antifraude, a exportação e a reposição de estoque (`python -m pytest backend/tests -v`) |

Rotas, exemplos e explicações: seção **Backend e banco de dados (Entrega 3)** do
README principal. Com o servidor no ar, **http://localhost:8000/docs** permite
testar cada rota pelo navegador.
