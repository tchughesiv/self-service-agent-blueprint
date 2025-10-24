{{/*
Environment variable helpers for consistent env var generation across Knative Services and Deployments
*/}}

{{/*
Generate database environment variables from pgvector secret
*/}}
{{- define "self-service-agent.dbEnvVars" -}}
{{/* Database Configuration */}}
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
{{/* Database Performance Configuration */}}
- name: DB_POOL_SIZE
  value: {{ if hasKey .Values.requestManagement "database" }}{{ .Values.requestManagement.database.poolSize | default "10" | quote }}{{ else }}"10"{{ end }}
- name: DB_MAX_OVERFLOW
  value: {{ if hasKey .Values.requestManagement "database" }}{{ .Values.requestManagement.database.maxOverflow | default "20" | quote }}{{ else }}"20"{{ end }}
- name: DB_POOL_TIMEOUT
  value: {{ if hasKey .Values.requestManagement "database" }}{{ .Values.requestManagement.database.poolTimeout | default "30" | quote }}{{ else }}"30"{{ end }}
- name: DB_POOL_RECYCLE
  value: {{ if hasKey .Values.requestManagement "database" }}{{ .Values.requestManagement.database.poolRecycle | default "3600" | quote }}{{ else }}"3600"{{ end }}
- name: DB_STATEMENT_TIMEOUT
  value: {{ if hasKey .Values.requestManagement "database" }}{{ .Values.requestManagement.database.statementTimeout | default "30000" | quote }}{{ else }}"30000"{{ end }}
- name: DB_IDLE_TRANSACTION_TIMEOUT
  value: {{ if hasKey .Values.requestManagement "database" }}{{ .Values.requestManagement.database.idleTransactionTimeout | default "300000" | quote }}{{ else }}"300000"{{ end }}
{{/* Sync Connection Pool (PostgresSaver/LangGraph) */}}
- name: DB_SYNC_POOL_MIN_SIZE
  value: {{ if hasKey .Values.requestManagement "database" }}{{ .Values.requestManagement.database.syncPoolMinSize | default "1" | quote }}{{ else }}"1"{{ end }}
- name: DB_SYNC_POOL_MAX_SIZE
  value: {{ if hasKey .Values.requestManagement "database" }}{{ .Values.requestManagement.database.syncPoolMaxSize | default "5" | quote }}{{ else }}"5"{{ end }}
- name: DB_SYNC_POOL_TIMEOUT
  value: {{ if hasKey .Values.requestManagement "database" }}{{ .Values.requestManagement.database.syncPoolTimeout | default "30" | quote }}{{ else }}"30"{{ end }}
{{- end }}

{{/*
Generate common environment variables for all services
*/}}
{{- define "self-service-agent.commonEnvVars" -}}
{{/* Application Configuration */}}
- name: LOG_LEVEL
  value: {{ .Values.logLevel | default "INFO" | quote }}
- name: SQL_DEBUG
  value: "false"
- name: EXPECTED_MIGRATION_VERSION
  value: {{ .Values.database.expectedMigrationVersion | default "001" | quote }}
{{/* Eventing Configuration - Always enabled (mock or full Knative) */}}
- name: BROKER_URL
  value: {{ if .Values.requestManagement.knative.eventing.enabled }}{{ printf "%s/%s/%s" .Values.requestManagement.knative.broker.url .Release.Namespace .Values.requestManagement.knative.broker.name | quote }}{{ else }}{{ printf "http://%s-mock-eventing.%s.svc.cluster.local:8080/%s/%s" (include "self-service-agent.fullname" .) .Release.Namespace .Release.Namespace .Values.requestManagement.knative.broker.name | quote }}{{ end }}
{{/* Server Configuration */}}
- name: PORT
  value: "8080"
- name: HOST
  value: "0.0.0.0"
{{- end }}

{{/*
Generate Request Manager specific environment variables
*/}}
{{- define "self-service-agent.requestManagerEnvVars" -}}
{{/* Event Processing Configuration */}}
- name: EVENT_MAX_RETRIES
  value: "3"
- name: EVENT_BASE_DELAY
  value: "1.0"
{{/* Integration API Keys */}}
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
{{/* JWT Authentication Configuration */}}
- name: JWT_ENABLED
  value: {{ .Values.security.jwt.enabled | default false | quote }}
- name: JWT_ISSUERS
  value: {{ .Values.security.jwt.issuers | toJson | quote }}
- name: JWT_VERIFY_SIGNATURE
  value: {{ .Values.security.jwt.validation.verifySignature | default true | quote }}
- name: JWT_VERIFY_EXPIRATION
  value: {{ .Values.security.jwt.validation.verifyExpiration | default true | quote }}
- name: JWT_VERIFY_AUDIENCE
  value: {{ .Values.security.jwt.validation.verifyAudience | default true | quote }}
- name: JWT_VERIFY_ISSUER
  value: {{ .Values.security.jwt.validation.verifyIssuer | default true | quote }}
- name: JWT_LEEWAY
  value: {{ .Values.security.jwt.validation.leeway | default 60 | quote }}
{{/* API Key Authentication Configuration */}}
- name: WEB_API_KEYS
  value: {{ .Values.security.apiKeys.webKeys | toJson | quote }}
{{/* Agent Service Configuration */}}
- name: AGENT_SERVICE_HOST
  value: "{{ include "self-service-agent.fullname" . }}-agent-service"
- name: AGENT_SERVICE_PORT
  value: "80"
{{- end }}

{{/*
Generate Agent Service specific environment variables
*/}}
{{- define "self-service-agent.agentServiceEnvVars" -}}
{{/* LLM Service Configuration */}}
- name: LLAMA_STACK_URL
  value: {{ .Values.llama_stack_url | default "http://llamastack:8321" | quote }}
{{/* Agent Configuration */}}
- name: DEFAULT_AGENT_ID
  value: {{ if hasKey .Values "agent" }}{{ .Values.agent.defaultAgentId | default "routing-agent" | quote }}{{ else }}"routing-agent"{{ end }}
- name: AGENT_TIMEOUT
  value: {{ if hasKey .Values "agent" }}{{ .Values.agent.timeout | default "120" | quote }}{{ else }}"120"{{ end }}
- name: ALWAYS_REFRESH_AGENT_MAPPING
  value: {{ if hasKey .Values "agent" }}{{ .Values.agent.alwaysRefreshMapping | default "true" | quote }}{{ else }}"true"{{ end }}
{{- end }}

{{/*
Generate Integration Dispatcher specific environment variables
*/}}
{{- define "self-service-agent.integrationDispatcherEnvVars" -}}
{{/* Slack Integration Configuration */}}
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
{{/* SMTP Configuration */}}
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
{{/* Test Integration Configuration */}}
- name: TEST_INTEGRATION_ENABLED
  value: {{ if hasKey .Values "testIntegrationEnabled" }}{{ .Values.testIntegrationEnabled | quote }}{{ else }}"true"{{ end }}
{{/* Integration User Defaults Configuration (auto-enabled based on health checks) */}}
{{- if hasKey .Values.requestManagement "integrations" }}
{{- if hasKey .Values.requestManagement.integrations "userDefaults" }}
{{- range $integrationType, $config := .Values.requestManagement.integrations.userDefaults }}
{{- if hasKey $config "enabled" }}
- name: INTEGRATION_DEFAULTS_{{ $integrationType }}_ENABLED
  value: {{ $config.enabled | quote }}
{{- end }}
- name: INTEGRATION_DEFAULTS_{{ $integrationType }}_PRIORITY
  value: {{ $config.priority | default 0 | quote }}
{{- if hasKey $config "retryCount" }}
- name: INTEGRATION_DEFAULTS_{{ $integrationType }}_RETRY_COUNT
  value: {{ $config.retryCount | default 3 | quote }}
{{- end }}
{{- if hasKey $config "retryDelaySeconds" }}
- name: INTEGRATION_DEFAULTS_{{ $integrationType }}_RETRY_DELAY_SECONDS
  value: {{ $config.retryDelaySeconds | default 60 | quote }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
{{/* Webhook default URL configuration */}}
{{- if and (hasKey .Values.requestManagement "integrations") (hasKey .Values.requestManagement.integrations "userDefaults") (hasKey .Values.requestManagement.integrations.userDefaults "WEBHOOK") (hasKey .Values.requestManagement.integrations.userDefaults.WEBHOOK "url") }}
- name: DEFAULT_WEBHOOK_URL
  value: {{ .Values.requestManagement.integrations.userDefaults.WEBHOOK.url | quote }}
{{- end }}
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
