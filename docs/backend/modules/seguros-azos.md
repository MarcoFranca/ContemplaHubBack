# Módulo Seguro de Vida Azos

## Objetivo e limite de domínio

O módulo `seguros-azos` opera Seguro de Vida como um domínio próprio. Ele compartilha somente o
CRM de `leads`, a organização, usuários e autenticação. Ele não lê nem escreve em `cotas`,
`contratos`, `lances`, `carteira_clientes` ou as tabelas de comissão do Consórcio.

A Azos disponibiliza cotação e consultas. A contratação final continua no canal autorizado pela
Azos; o ContemplaHub não a representa como venda concluída antes da sincronização de proposta ou
apólice.

## Proposta pública e interesse do cliente

Uma cotação pode ser publicada por `POST /seguros/azos/cotacoes/{cotacao_id}/publicar`. O backend
gera um hash criptograficamente aleatório e devolve a URL pública e uma mensagem sugerida para
WhatsApp. A página usa `GET /seguros/azos/p/{hash}` e recebe somente o primeiro nome do cliente,
prêmio e coberturas, sem perfil de cotação, contatos, IDs internos ou dados da organização.

`POST /seguros/azos/p/{hash}/interesse` é público e idempotente: altera o status da cotação para
`interesse_confirmado` e cria/atualiza um único registro em `seguro_azos_atendimentos`, com status
`pendente`. Na primeira confirmação, o sistema também tenta avisar o e-mail configurado da
organização; uma falha de e-mail nunca desfaz o interesse registrado. Esse alerta é exclusivamente
do domínio Seguro Azos; ele não cria contrato, cadastro, proposta ou atividade de Consórcio. A
formalização final permanece no canal autorizado da Azos.

## Integração

### Carteira de corretor e comissões

Como a credencial configurada pertence ao corretor parceiro, a carteira é sincronizada pelos
endpoints de corretor da Azos: `GET /v1/brokers/policies` e
`GET /v1/brokers/commissions`. O endpoint interno
`POST /seguros/azos/carteira/sincronizar` exige perfil manager e atualiza as cópias locais de
apólices e comissões para a organização autenticada. `GET /seguros/azos/carteira` exige usuário
interno e devolve somente os campos operacionais necessários para a interface.
O sincronismo percorre todas as páginas retornadas pela Azos a partir do `offset` informado, para
não importar apenas a primeira página de uma carteira maior.

`seguro_azos_apolices` é a carteira de Seguro: traz situação, vigência, prêmio, atraso e URL
oficial da apólice. A URL da Azos é a referência principal; um upload manual é exceção operacional.
Durante a sincronização de corretor, o CPF de `insured_data.cpf` é normalizado e comparado apenas
com `lead_cadastros_pf.cpf` da mesma organização. A apólice recebe `lead_id` somente quando o CPF
corresponde a um único lead; CPF ausente, inválido ou ambíguo permanece sem associação automática.
`seguro_azos_comissoes` guarda os registros externos de comissão, por status Azos, sem escrever em
`comissao_lancamentos`, repasses ou regras de comissão do Consórcio. Uma futura visão consolidada
deve apenas somar leituras dos dois domínios, nunca unificar suas tabelas ou status.

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
