# Módulo WhatsApp (Cloud API oficial)

## Responsabilidade

Automação de WhatsApp no CRM por organização, usando a **WhatsApp Cloud API oficial** da Meta.
Objetivo: enviar mensagem de boas-vindas automática a todo lead novo e (Fase 3) capturar/responder
quem chega pelo anúncio Click-to-WhatsApp.

## Decisões

- **Oficial (Cloud API)**, não solução não oficial: qualidade, status de entrega/leitura, webhooks
  estruturados e sem risco de ban.
- **Número dedicado** por org (não usar o número pessoal, que sairia do app do WhatsApp).
- Conexão por **Embedded Signup** (FB JS SDK no frontend → code → troca por token no backend).
- Template de boas-vindas **configurável por org**.
- Disparo por **trigger no banco** (não em cada rota) para pegar todo lead novo de qualquer origem.
- Fila drenada por **cron do Railway** (dispatcher), não síncrono, para não acoplar o cadastro do
  lead à API do WhatsApp.

## Tabelas (migration 013_whatsapp.sql)

- `whatsapp_integrations` — conexão da WABA/número por org. `access_token`/`verify_token` nunca
  expostos ao frontend.
- `whatsapp_templates` — template configurável por org (espelho do aprovado na Meta), `key = lead_welcome`.
- `whatsapp_messages` — log de mensagens in/out e status de entrega.
- `whatsapp_outbound_queue` — fila de disparo (dedupe por `dedupe_key`).

Trigger `trg_enqueue_whatsapp_welcome` (AFTER INSERT em `leads`): enfileira boas-vindas quando o lead
tem telefone válido, `origem <> whatsapp` e a org tem integração ativa.

## Fluxo de conexão (Fase 1)

1. Frontend chama `GET /whatsapp/config` (app_id, config_id, graph_version).
2. Frontend inicia o Embedded Signup (FB JS SDK) e captura `code` + `waba_id` + `phone_number_id`.
3. `POST /whatsapp/connect` troca o code por token de negócio, enriquece com dados do número/WABA,
   inscreve o app nos webhooks da WABA e persiste a integração.
4. `GET/PUT /whatsapp/template` gerenciam a mensagem de boas-vindas.

## Envs

- `META_APP_ID` / `META_APP_SECRET` (reaproveitados do Meta Lead Ads).
- `WHATSAPP_ES_CONFIG_ID` — config_id do fluxo de Embedded Signup criado no app da Meta.
- `WHATSAPP_VERIFY_TOKEN` — verificação do webhook público.
- `WHATSAPP_DISPATCH_SECRET` — autentica o dispatcher (cron) no endpoint interno (Fase 2).
- `WHATSAPP_OAUTH_SCOPES` — default `whatsapp_business_management,whatsapp_business_messaging,business_management`.

## Roadmap

- **Fase 1 (feito):** banco + conexão (Embedded Signup + manual) + template + tela de configuração.
- **Fase 2 (feito):** dispatcher drenando `whatsapp_outbound_queue` → envio via Cloud API, log em
  `whatsapp_messages`, retry com backoff. Sem template aprovado, usa `hello_world`.
  - **Agendador embutido (padrão):** o backend roda `process_outbound_queue` a cada
    `WHATSAPP_DISPATCH_INTERVAL_SEC` segundos (default 60; `0` desliga). Não precisa de cron externo.
  - **Cron externo (opcional):** `POST /whatsapp/dispatch` com header `X-Dispatch-Secret` =
    `WHATSAPP_DISPATCH_SECRET`. Use se preferir desligar o embutido (`WHATSAPP_DISPATCH_INTERVAL_SEC=0`).
  - `POST /whatsapp/test-send` valida a conexão na hora (envia imediatamente).
- **Fase 3 (feito):** `POST /api/public/webhooks/whatsapp` processa `messages` (inbound) e `statuses`.
  Inbound: resolve a org por `phone_number_id`, cria/dedup lead (`origem=whatsapp`, `etapa=novo`,
  `channel=whatsapp`), loga em `whatsapp_messages` (`direction=in`) e, **só no primeiro contato**,
  envia auto-resposta de texto livre (janela 24h, gratuita) com o corpo do template. Idempotente por
  `wa_message_id`. Status (sent/delivered/read/failed) atualizam a mensagem correspondente. Assinatura
  `X-Hub-Signature-256` validada com `WHATSAPP_APP_SECRET` quando configurado. Sempre responde 200.
## Agente de IA (Claude)

Nativo, dentro do backend (`app/ai/`). Governança em 2 camadas:
- **GLOBAL (só super admin / repo):** base de conhecimento em `app/ai/knowledge/*.md` (negócio, produtos,
  público, objeções, FAQ, qualificação, processo, compliance BACEN/LGPD, tom) + guardrails no system prompt.
  Imutável para as orgs.
- **POR ORG (runtime):** administradoras da org e dados do lead injetados; toggle `ai_enabled` por org.

Fluxo: webhook inbound → se `WHATSAPP_AI_ENABLED` (master) e `whatsapp_integrations.ai_enabled` (org) e o
lead não estiver em handoff → `agent.run_agent` roda o loop de tool use do Claude sobre o histórico da
conversa e responde. Ferramentas (`app/ai/tools.py`): `simular_consorcio` (porte do simulador),
`registrar_qualificacao` (grava em `lead_interesses` + atividade), `buscar_dados_lead`, `escalar_humano`
(loga atividade + marca a conversa com `payload.ai_handoff=true`, e a IA fica em silêncio para aquele lead).
Modelo configurável em `WHATSAPP_AI_MODEL` (default `claude-sonnet-5`); `ANTHROPIC_API_KEY` obrigatória.
Endpoint `POST /whatsapp/ai/toggle` liga/desliga por org (migration 015 adiciona `ai_enabled`).

Fallback: se a IA não responder (desligada, erro ou sem chave), mantém a auto-resposta fixa do 1º contato.

### Áudio (voz)

`app/ai/audio.py` — provedor de áudio plugável (hoje OpenAI: Whisper STT + TTS; ElevenLabs previsto).
- **Cliente manda áudio** → `download_media` baixa do WhatsApp → `transcrever()` (Whisper) → o texto vira a
  mensagem que a IA processa (e fica legível no inbox). Se não transcrever, cai no fallback "me manda por texto".
- **Espelhar modalidade** (`WHATSAPP_AUDIO_REPLY`): se a origem foi áudio, a resposta da IA vira voz
  (`sintetizar()` TTS → `upload_media` → `send_audio_message`), com o texto logado junto. Se o TTS falhar,
  cai para texto.
- **"Digitando..."**: `send_typing_indicator` mostra o indicador antes da resposta (marca lida + typing).
- **Voz invertida por gênero**: `_voz_por_genero(nome)` escolhe a voz TTS pelo 1º nome do cliente
  (homem → voz feminina; mulher → voz masculina; indefinido → feminina). Heurística PT-BR (termina em "a" =
  feminino) com listas de exceção. Configurável por `OPENAI_TTS_VOICE_FEMININA` (default `nova`) e
  `OPENAI_TTS_VOICE_MASCULINA` (default `onyx`).
- Envs: `OPENAI_API_KEY`, `WHATSAPP_AUDIO_ENABLED`, `WHATSAPP_AUDIO_REPLY`, `AUDIO_TTS_PROVIDER`,
  `OPENAI_STT_MODEL`, `OPENAI_TTS_MODEL`, `OPENAI_TTS_VOICE`, `OPENAI_TTS_VOICE_FEMININA`,
  `OPENAI_TTS_VOICE_MASCULINA` (e `ELEVENLABS_*` quando trocar a voz).
  Custo do áudio é do provedor externo (OpenAI), separado do Claude.

### Funil no kanban (Fase A)

A IA mantém o kanban atualizado sozinha via a ferramenta `atualizar_etapa_classificacao`
(`app/ai/tools.py`), chamada em segundo plano conforme a conversa evolui:
- **Move `leads.etapa`** — só etapas permitidas à IA: `novo`, `tentativa_contato`, `contato_realizado`,
  `diagnostico`, `proposta`, `negociacao`, `frio`. Fechamento (`contrato`/`ativo`/`pos_venda`) e `perdido`
  ficam para o humano.
- **Classifica `leads.temperatura`** (`frio`/`morno`/`quente`) + `temperatura_at`. Coluna `text` com CHECK
  (não enum, para evitar o cache do PostgREST). Migration `016_lead_temperatura.sql`.
- **`valor_agregado`** grava em `leads.valor_interesse` quando conhecido.
- Cada atualização também loga uma `activity` (tipo `whatsapp`).

Decisão de arquitetura: **funil único** compartilhado + filtro "canal = WhatsApp" (não um funil separado).
Fases B/C (pendentes): badge de temperatura + filtro no kanban do front; faixa de status de atendimento
(IA atendendo / precisa humano / aguardando / reengajar) integrando o handoff.

### Follow-up automático e lembretes

`app/services/whatsapp_followup_service.py` roda no agendador embutido (`_whatsapp_dispatch_loop`,
com throttle de `FOLLOWUP_SWEEP_INTERVAL_SEC`, default 300s) e também via `POST /whatsapp/followups/run`
(protegido por `X-Dispatch-Secret`).

- **Follow-up:** reengaja leads que ficaram sem responder. Deriva o estado das próprias mensagens (sem
  tabela nova): conta mensagens de saída com `payload.followup=true` desde a última mensagem recebida.
  Só age dentro da janela de 24h, quando a última mensagem foi nossa, respeitando um intervalo mínimo
  (`FOLLOWUP_MIN_GAP_HOURS`) e um máximo de tentativas (`FOLLOWUP_MAX_ATTEMPTS`). Pula leads com
  `nao_perturbe`, em etapa terminal, com reunião futura ou em handoff.
- **Lembretes:** varre `agendamentos` ativos e envia lembrete ~24h e ~1h antes (dedup via
  `lembrete_24h_at` / `lembrete_1h_at`). **Só envia dentro da janela de 24h** (fora dela a mensagem livre
  não é entregue; exigiria template aprovado, ainda não disponível): nesse caso apenas marca a coluna e
  loga `reminder_fora_da_janela`.
- Envs: `FOLLOWUP_ENABLED`, `FOLLOWUP_MAX_ATTEMPTS`, `FOLLOWUP_MIN_GAP_HOURS`, `FOLLOWUP_WINDOW_HOURS`,
  `REMINDER_ENABLED`, `FOLLOWUP_SWEEP_INTERVAL_SEC`. Migration `022_agenda_lembretes.sql`.
- Pendente: envio fora da janela via template aprovado; detecção de CTWA (janela de 72h).

## Timeline / inbox

- **Fase 4 (feito):** timeline de conversa no detalhe do lead. O frontend lê `whatsapp_messages`
  direto via `supabaseServer` (RLS `org_id = app_org_id()`) e renderiza bolhas in/out com status
  (enviado/entregue/lido/falhou) e um selo de janela de 24h (baseado na última mensagem recebida).
  Arquivo: `front/.../app/leads/[leadId]/WhatsappConversationCard.tsx`.
