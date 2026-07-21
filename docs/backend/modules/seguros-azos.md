# Módulo Seguro de Vida Azos

## Objetivo e limite de domínio

O módulo `seguros-azos` opera Seguro de Vida como um domínio próprio. Ele compartilha somente o
CRM de `leads`, a organização, usuários e autenticação. Ele não lê nem escreve em `cotas`,
`contratos`, `lances`, `carteira_clientes` ou as tabelas de comissão do Consórcio.

A Azos disponibiliza cotação e consultas. A contratação final continua no canal autorizado pela
Azos; o ContemplaHub não a representa como venda concluída antes da sincronização de proposta ou
apólice.

## Integração

- base: `AZOS_API_BASE_URL`, padrão `https://api.gateway.azos.com.br`;
- autenticação: header server-side `X-API-KEY`, vindo exclusivamente de `AZOS_API_KEY`;
- nenhuma chave é persistida no banco, retornada por endpoint ou exposta ao frontend/mobile;
- não há webhook Azos na especificação recebida. Propostas e apólices são sincronizadas por
  requisição manual, preparada para posterior agendamento.

### Cotação

1. `GET /v1/platform/quotation/professions` lista profissões e seus IDs Azos.
2. `POST /v1/platform/quotation/coverages` recebe data de nascimento, sexo, altura, peso,
   fumante, renda e `profession_id`; devolve coberturas elegíveis e regras de capital.
3. `POST /v1/platform/quotation/coverages2premiums` recebe o mesmo perfil e os capitais
   selecionados; devolve prêmio mensal/anual e erros por cobertura.

O perfil requer confirmação explícita de consentimento antes de ser enviado. O backend mantém o
mínimo necessário para reproduzir a cotação, não imprime o payload em logs e restringe as rotas a
usuários internos da organização.

### Sincronização posterior à contratação

- propostas: `GET /v1/platforms/proposals`;
- apólices: `GET /v1/platforms/policies`.

As cópias locais guardam o identificador Azos, status, timestamp externo e payload recebido. A
associação automática a um lead não é feita por nome/telefone para evitar falso vínculo; ela fica
vazia até o fluxo operacional fornecer uma referência confiável.

## Estados externos observados

As propostas Azos podem retornar, entre outros, `draft`, `sign_process`, `info_completed`,
`signed`, `in_analysis`, `pending_payment`, `accepted`, `expired`, `refused`, `discontinued`,
`counter_proposal` e `filed`. Esses valores são preservados como status externo, não são estados
do funil de Consórcio.

## Próximas etapas previstas

- tela própria de Seguro de Vida no frontend;
- associação explícita de propostas/apólices Azos ao lead;
- sincronização agendada e consulta de faturas/comissões Azos;
- definição operacional de retenção e descarte de perfis de cotação.
