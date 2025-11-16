@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Friendly environment name used for resource naming.')
param environmentName string = 'agent'

@description('Container image to deploy for the agent app. This is set automatically by azd.')
param agentappImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Azure AI Foundry project endpoint that the agent uses.')
param azureAiProjectEndpoint string = ''

@description('Azure AI Foundry model deployment name to use when creating ephemeral agents.')
param azureAiModelDeploymentName string = ''

@description('Optional existing Azure AI Foundry agent identifier. Leave blank to create ephemeral agents.')
param azureAiAgentId string = ''

var nameSuffix = uniqueString(resourceGroup().id, environmentName)
var safeEnv = toLower(replace(environmentName, '_', '-'))
var envToken = safeEnv == '' ? 'agent' : safeEnv
var envSegment = substring(envToken, 0, min(length(envToken), 12))
var uniqueSegment = substring(nameSuffix, 0, 13)
var rawAlnumEnv = replace(envToken, '-', '')
var normalizedAlnumEnv = rawAlnumEnv == '' ? 'agent' : rawAlnumEnv
var alnumEnvSegment = substring(normalizedAlnumEnv, 0, min(length(normalizedAlnumEnv), 10))
var workspaceName = toLower(format('{0}-law-{1}', envSegment, uniqueSegment))
var appInsightsName = toLower(format('{0}-appi-{1}', envSegment, uniqueSegment))
var containerAppEnvironmentName = toLower(format('{0}-cae-{1}', envSegment, uniqueSegment))
var containerAppName = toLower(format('{0}-agent-{1}', envSegment, uniqueSegment))
var containerRegistryName = toLower(format('acr{0}{1}', alnumEnvSegment, uniqueSegment))
var normalizedAgentImage = toLower(agentappImage)
var normalizedRegistryServer = toLower(format('{0}.azurecr.io', containerRegistryName))
var useAcrRegistry = startsWith(normalizedAgentImage, normalizedRegistryServer)
var containerRegistries = useAcrRegistry ? [
  {
    server: containerRegistryLoginServer
    identity: 'system'
  }
] : []

var logAnalyticsSku = 'PerGB2018'
var workspaceRetentionInDays = 30

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: workspaceName
  location: location
  properties: {
    sku: {
      name: logAnalyticsSku
    }
    retentionInDays: workspaceRetentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
  tags: {
    'azd-env-name': environmentName
    'azd-service': 'agentapp'
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
  }
  tags: {
    'azd-env-name': environmentName
    'azd-service': 'agentapp'
  }
}

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: containerRegistryName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    adminUserEnabled: false
  }
  identity: {
    type: 'SystemAssigned'
  }
  tags: {
    'azd-env-name': environmentName
  }
}

var containerRegistryLoginServer = format('{0}.azurecr.io', containerRegistryName)

resource containerAppEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerAppEnvironmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
  tags: {
    'azd-env-name': environmentName
  }
}

resource agentapp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppEnvironment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 80
        transport: 'auto'
      }
      registries: containerRegistries
      secrets: [
        {
          name: 'application-insights-connection-string'
          value: appInsights.properties.ConnectionString
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'agentapp'
          image: agentappImage
          env: [
            {
              name: 'APPLICATION_INSIGHTS_CONNECTION_STRING'
              secretRef: 'application-insights-connection-string'
            }
            {
              name: 'AZURE_AI_PROJECT_ENDPOINT'
              value: azureAiProjectEndpoint
            }
            {
              name: 'AZURE_AI_MODEL_DEPLOYMENT_NAME'
              value: azureAiModelDeploymentName
            }
            {
              name: 'AZURE_AI_AGENT_ID'
              value: azureAiAgentId
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        maxReplicas: 2
        minReplicas: 0
      }
    }
  }
  tags: {
    'azd-env-name': environmentName
    'azd-service': 'agentapp'
  }
}

resource registryRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, containerAppName, 'AcrPull')
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') // AcrPull
    principalId: agentapp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

@description('Azure Container Apps ingress FQDN for the agent service.')
output service__agentapp__host string = agentapp.properties.configuration.ingress.fqdn

@description('Azure Container Apps resource ID for the agent service.')
output service__agentapp__resourceId string = agentapp.id

@description('Azure Container Apps name for the agent service.')
output service__agentapp__name string = containerAppName

@description('System-assigned managed identity principal ID for the agent service.')
output service__agentapp__principalId string = agentapp.identity.principalId

@description('Azure Container Registry login server hosting the service images.')
output containerRegistryLoginServer string = containerRegistryLoginServer

@description('Azure Container Registry resource ID.')
output containerRegistryResourceId string = containerRegistry.id

@description('Application Insights connection string for telemetry.')
output applicationInsightsConnectionString string = appInsights.properties.ConnectionString
