# Banco de dados — SQLite (Entrega 3)

| Arquivo | O que faz |
|---|---|
| `schema.sql` | tabelas, chaves, restrições (`CHECK`, `UNIQUE`), índices, gatilhos de auditoria e a visão `vw_resumo_caixa` |
| `seed.sql` | carga de teste: clientes, contas, cartões da bancada, produtos e o terminal POS-01 |
| `bytebank.db` | o banco em si, criado pelo backend na primeira execução (fora do GitHub) |

O backend cria o banco sozinho (`python -m backend`). Para criar à mão:

```bash
sqlite3 database/bytebank.db < database/schema.sql
sqlite3 database/bytebank.db < database/seed.sql
```

O diagrama das tabelas e as regras que o banco garante estão na seção
**Modelo de dados** do README principal.
