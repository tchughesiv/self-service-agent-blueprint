{{/*
Expand the name of the chart.
*/}}
{{- define "self-service-agent.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "self-service-agent.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "self-service-agent.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "self-service-agent.labels" -}}
helm.sh/chart: {{ include "self-service-agent.chart" . }}
{{ include "self-service-agent.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "self-service-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ include "self-service-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "self-service-agent.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "self-service-agent.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Override mcp-servers.canDeployMCPServer to avoid cluster-scoped namespace permissions.

The original implementation requires cluster-admin to list all namespaces or get a specific
namespace. Since Helm's lookup function throws errors on permission denial (which can't be
caught in templates), we simply return false here.

This is safe because:
1. If deploymentMode is "deployment" (as in our values.yaml), this check's result is ignored
2. If deploymentMode is "auto", returning false just means it will use Deployments instead
   of Toolhive MCPServer CRDs, which is fine since we don't have Toolhive anyway

Users who need Toolhive can override this helper in their own charts with proper permissions.
*/}}
{{- define "mcp-servers.canDeployMCPServer" -}}
  {{- "false" }}
{{- end }}
