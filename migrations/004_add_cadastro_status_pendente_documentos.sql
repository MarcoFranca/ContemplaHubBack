-- 2025XXXXXX_add_cadastro_status_pendente_documentos.sql
ALTER TYPE cadastro_status
ADD VALUE IF NOT EXISTS 'pendente_documentos';
