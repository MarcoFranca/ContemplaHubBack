# AGENTS.md

## Contexto do projeto
Este repositório é o backend do ContemplaHub.

Stack principal:
- FastAPI
- Supabase / Postgres
- Drizzle para schema/migrations
- arquitetura multi-tenant com `org_id`

## Regras obrigatórias de domínio
- Toda tabela de negócio deve respeitar `org_id`
- Backend sempre valida autorização além do RLS
- Nunca assumir acesso inseguro
- Não usar service role em código cliente
- Não quebrar separação entre:
  - leads
  - cotas
  - contratos
  - comissões
  - parceiros
  - carteira

## Regras de banco e migrations
- Nunca renomear ou remover colunas/tabelas sem necessidade explícita
- Preferir migrations pequenas, seguras e reversíveis
- Preservar compatibilidade com fluxos existentes
- Não criar novas tabelas fora do escopo sem justificativa clara
- Manter coerência com o schema real e com o domínio do produto

## Regras de código
- Seguir o padrão já existente no projeto
- Evitar refactors amplos fora do escopo pedido
- Preferir mudanças cirúrgicas
- Preservar nomes e contratos existentes quando possível
- Ao encontrar ambiguidade, escolher a solução mais compatível com o modelo atual

## Validação obrigatória
Sempre que alterar código, rodar os checks relevantes do projeto.
No mínimo, localizar e executar:
- lint
- typecheck
- testes relevantes
- checks específicos citados neste repositório

Se algum check falhar, fazer melhor esforço para corrigir antes de concluir.

## Documentação viva (obrigatório)
Sempre que a tarefa alterar qualquer um dos itens abaixo, atualizar a documentação correspondente no mesmo task:
- endpoints
- payloads
- DTOs / schemas
- regras de negócio
- migrations
- tabelas / colunas
- autenticação / autorização
- fluxos operacionais
- integrações externas

## Estrutura de documentação
Usar e manter documentação em `docs/backend/`.

Estrutura esperada:
- `docs/backend/README.md`
- `docs/backend/architecture.md`
- `docs/backend/api/endpoints.md`
- `docs/backend/database/schema-overview.md`
- `docs/backend/database/multi-tenant-rls.md`
- `docs/backend/modules/*.md`

## Ao final de cada task
Sempre entregar um resumo com:
1. arquivos alterados
2. migrations criadas/alteradas
3. comandos executados
4. resultados dos checks
5. riscos ou follow-ups
6. arquivos de documentação alterados