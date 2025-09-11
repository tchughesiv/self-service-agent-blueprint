{{/*
Environment variable helpers for consistent env var generation across Knative Services and Deployments
*/}}

{{/*
Generate database environment variables from pgvector secret
*/}}
{{- define "self-service-agent.dbEnvVars" -}}
- name: POSTGRES_HOST
  valueFrom:
    secretKeyRef:
      name: pgvector
      key: host
- name: POSTGRES_PORT
  valueFrom:
    secretKeyRef:
      name: pgvector
      key: port
- name: POSTGRES_DB
  valueFrom:
    secretKeyRef:
      name: pgvector
      key: dbname
- name: POSTGRES_USER
  valueFrom:
    secretKeyRef:
      name: pgvector
      key: user
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: pgvector
      key: password
{{- end }}

{{/*
Generate common environment variables for all services
*/}}
{{- define "self-service-agent.commonEnvVars" -}}
- name: LOG_LEVEL
  value: "INFO"
- name: SQL_DEBUG
  value: "false"
- name: EXPECTED_MIGRATION_VERSION
  value: {{ .Values.database.expectedMigrationVersion | default "001" | quote }}
- name: BROKER_URL
  value: {{ printf "%s/%s/%s" .Values.requestManagement.knative.broker.url .Release.Namespace .Values.requestManagement.knative.broker.name | quote }}
- name: PORT
  value: "8080"
- name: HOST
  value: "0.0.0.0"
{{- end }}

{{/*
Generate Request Manager specific environment variables
*/}}
{{- define "self-service-agent.requestManagerEnvVars" -}}
- name: EVENT_MAX_RETRIES
  value: "3"
- name: EVENT_BASE_DELAY
  value: "1.0"
# Service Mesh and Security Configuration
- name: SLACK_SIGNING_SECRET
  valueFrom:
    secretKeyRef:
      name: slack-signing-secret
      key: signing-secret
      optional: true
- name: SNOW_API_KEY
  valueFrom:
    secretKeyRef:
      name: api-keys
      key: snow-integration
      optional: true
- name: HR_API_KEY
  valueFrom:
    secretKeyRef:
      name: api-keys
      key: hr-system
      optional: true
- name: MONITORING_API_KEY
  valueFrom:
    secretKeyRef:
      name: api-keys
      key: monitoring-system
      optional: true
{{- end }}

{{/*
Generate Agent Service specific environment variables
*/}}
{{- define "self-service-agent.agentServiceEnvVars" -}}
- name: LLAMA_STACK_URL
  value: {{ .Values.llama_stack_url | default "http://llamastack:8321" | quote }}
- name: DEFAULT_AGENT_ID
  value: "routing-agent"
- name: AGENT_TIMEOUT
  value: "120"
{{- end }}

{{/*
Generate Integration Dispatcher specific environment variables
*/}}
{{- define "self-service-agent.integrationDispatcherEnvVars" -}}
- name: EMAIL_SMTP_HOST
  value: {{ .Values.requestManagement.integrations.email.smtpHost | default "smtp.example.com" | quote }}
- name: EMAIL_SMTP_PORT
  value: {{ .Values.requestManagement.integrations.email.smtpPort | default "587" | quote }}
# Integration-specific environment variables (optional - loaded from secrets)
- name: SLACK_BOT_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "self-service-agent.fullname" . }}-integration-secrets
      key: slack-bot-token
      optional: true
- name: SLACK_SIGNING_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "self-service-agent.fullname" . }}-integration-secrets
      key: slack-signing-secret
      optional: true
# Additional SMTP configuration from secrets (matching Knative Service)
- name: SMTP_HOST
  valueFrom:
    secretKeyRef:
      name: {{ include "self-service-agent.fullname" . }}-integration-secrets
      key: smtp-host
      optional: true
- name: SMTP_PORT
  valueFrom:
    secretKeyRef:
      name: {{ include "self-service-agent.fullname" . }}-integration-secrets
      key: smtp-port
      optional: true
- name: SMTP_USERNAME
  valueFrom:
    secretKeyRef:
      name: {{ include "self-service-agent.fullname" . }}-integration-secrets
      key: smtp-username
      optional: true
- name: SMTP_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "self-service-agent.fullname" . }}-integration-secrets
      key: smtp-password
      optional: true
- name: SMTP_USE_TLS
  valueFrom:
    secretKeyRef:
      name: {{ include "self-service-agent.fullname" . }}-integration-secrets
      key: smtp-use-tls
      optional: true
- name: FROM_EMAIL
  valueFrom:
    secretKeyRef:
      name: {{ include "self-service-agent.fullname" . }}-integration-secrets
      key: from-email
      optional: true
- name: FROM_NAME
  valueFrom:
    secretKeyRef:
      name: {{ include "self-service-agent.fullname" . }}-integration-secrets
      key: from-name
      optional: true
{{- end }}

{{/*
Generate all environment variables for Request Manager
*/}}
{{- define "self-service-agent.requestManagerAllEnvVars" -}}
{{- include "self-service-agent.dbEnvVars" . }}
{{- include "self-service-agent.commonEnvVars" . }}
{{- include "self-service-agent.requestManagerEnvVars" . }}
{{- end }}

{{/*
Generate all environment variables for Agent Service
*/}}
{{- define "self-service-agent.agentServiceAllEnvVars" -}}
{{- include "self-service-agent.dbEnvVars" . }}
{{- include "self-service-agent.commonEnvVars" . }}
{{- include "self-service-agent.agentServiceEnvVars" . }}
{{- end }}

{{/*
Generate all environment variables for Integration Dispatcher
*/}}
{{- define "self-service-agent.integrationDispatcherAllEnvVars" -}}
{{- include "self-service-agent.dbEnvVars" . }}
{{- include "self-service-agent.commonEnvVars" . }}
{{- include "self-service-agent.integrationDispatcherEnvVars" . }}
{{- end }}
