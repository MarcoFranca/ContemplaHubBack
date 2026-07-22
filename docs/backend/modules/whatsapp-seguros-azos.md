# WhatsApp: Seguro de Vida Azos

O agente de WhatsApp possui um fluxo de Seguro de Vida Azos separado do fluxo de Consórcio. Ele não
chama simulador, proposta, contrato, carteira ou comissões de Consórcio.

Para cotar, coleta data de nascimento, sexo, altura, peso, profissão atual, renda mensal e tabagismo.
Antes de consultar a Azos, exige confirmação explícita do cliente autorizando o uso desses dados.
Perfis parciais não são persistidos.

O agente busca a profissão no catálogo Azos, consulta coberturas elegíveis e só calcula a cotação
com coberturas e capitais escolhidos entre as opções retornadas pela Azos. A ferramenta publica a
cotação em link público próprio de Seguro.

Quando a sincronização de propostas encontra uma `proposal_url` oficial da Azos, a proposta é
associada ao lead somente por CPF único na mesma organização. Havendo WhatsApp ativo, o backend
baixa o PDF em domínio Azos, envia-o como documento e marca `pdf_sent_at`; o mesmo PDF não é
reenviado em sincronizações futuras. Proposta sem CPF, CPF ambíguo ou telefone não recebe envio.
Essa verificação também ocorre no botão existente de sincronização da carteira Azos.

Quando o cliente confirma que quer seguir, o agente cria ou atualiza o atendimento pendente em
`seguro_azos_atendimentos` com origem `whatsapp_ia`, registra atividade no CRM e faz handoff para
o corretor. A IA permanece em silêncio após o handoff e a contratação final ocorre somente no canal
autorizado da Azos.
