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

## Coleta guiada e recomendação

O agente coleta uma informação por mensagem e reaproveita o que já foi informado. Além dos dados
obrigatórios da API, pergunta sobre autonomia profissional, filhos/dependentes, dívidas, reserva e
orçamento mensal. Não envia perfis parciais à Azos e exige consentimento explícito antes da consulta.

Após receber as coberturas elegíveis, `montar_recomendacao_vida_azos` dimensiona referências de
capital por continuidade de renda, compromissos financeiros e perfil familiar/profissional. Cada
capital é limitado ao mínimo, máximo e múltiplo devolvidos pela Azos. A sugestão é explicável,
ajustável e não representa aceitação, recomendação da seguradora ou garantia de indenização.

O follow-up identifica o produto mais recente nas mensagens e usa cadência própria para
`seguro_azos` ou `consorcio`. Fora da janela de mensagens livres, Seguro pode usar o template Meta
configurado em `FOLLOWUP_SEGURO_TEMPLATE_NAME`; sem template aprovado, não há disparo.

## Proposta detalhada

A migration `030_seguros_azos_recomendacao.sql` adiciona `recommendation` e
`recommendation_context` à cotação. A página pública mostra a lógica geral, motivo e prioridade de
cada cobertura, capital sugerido, prêmio calculado e aviso de que os valores podem subir ou descer
conforme preferência, orçamento, subscrição e teto liberado pela Azos.
