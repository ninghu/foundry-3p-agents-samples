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

@description('Resource ID of the Azure AI (Cognitive Services) account backing the project. Leave blank to skip automatic role assignment.')
param azureAiAccountResourceId string = ''

@description('Free-form marker to force redeployments when updated.')
param deploymentMarker string = '2025-11-18.1'

@description('Automatically create a dedicated Azure AI Foundry account and project when an existing account ID is not provided.')
param provisionAzureAiResources bool = true

@description('Enable the Azure AI Agents capability host when provisioning a new Azure AI account.')
param enableAzureAiHostedAgents bool = true

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
var containerAppIdentityName = toLower(format('{0}-id-{1}', envSegment, uniqueSegment))
var generatedAzureAiAccountName = toLower(format('{0}-ai-{1}', envSegment, uniqueSegment))
var generatedAzureAiProjectName = toLower(format('{0}-proj-{1}', envSegment, uniqueSegment))
var shouldProvisionAzureAi = provisionAzureAiResources && azureAiAccountResourceId == ''
var placeholderAzureAiAccountResourceId = '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/placeholder/providers/Microsoft.CognitiveServices/accounts/placeholder'
var usingExistingAzureAi = azureAiAccountResourceId != ''
var generatedAzureAiAccountResourceId = resourceId('Microsoft.CognitiveServices/accounts', generatedAzureAiAccountName)
var effectiveAzureAiAccountResourceId = usingExistingAzureAi ? azureAiAccountResourceId : (shouldProvisionAzureAi ? generatedAzureAiAccountResourceId : '')
var hasAzureAiAccount = effectiveAzureAiAccountResourceId != ''
var azureAiAccountSegments = split(hasAzureAiAccount ? effectiveAzureAiAccountResourceId : placeholderAzureAiAccountResourceId, '/')
var azureAiSubscriptionId = azureAiAccountSegments[2]
var azureAiResourceGroupName = azureAiAccountSegments[4]
var azureAiAccountNameFromId = azureAiAccountSegments[8]
var resolvedAzureAiAccountName = hasAzureAiAccount ? (usingExistingAzureAi ? azureAiAccountNameFromId : generatedAzureAiAccountName) : ''
var resolvedAzureAiProjectName = shouldProvisionAzureAi ? generatedAzureAiProjectName : ''

module managedAzureAi 'modules/azure-ai-project.bicep' = if (shouldProvisionAzureAi) {
  name: format('azureAiProject-{0}', uniqueString(resourceGroup().id, environmentName))
  params: {
    location: location
    tags: {
      'azd-env-name': environmentName
      'azd-service': 'agentapp'
    }
    accountName: generatedAzureAiAccountName
    projectName: generatedAzureAiProjectName
    enableHostedAgents: enableAzureAiHostedAgents
  }
}

var resolvedAzureAiProjectEndpoint = shouldProvisionAzureAi ? managedAzureAi.outputs.projectEndpoint : azureAiProjectEndpoint

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
    'deploy-marker': deploymentMarker
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
    'deploy-marker': deploymentMarker
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
    'deploy-marker': deploymentMarker
  }
}

var containerRegistryLoginServer = format('{0}.azurecr.io', containerRegistryName)

resource agentappIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: containerAppIdentityName
  location: location
  tags: {
    'azd-env-name': environmentName
    'azd-service': 'agentapp'
    'deploy-marker': deploymentMarker
  }
}

var containerRegistries = [
  {
    server: containerRegistryLoginServer
    identity: agentappIdentity.id
  }
]

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
    type: 'SystemAssigned,UserAssigned'
    userAssignedIdentities: {
      '${agentappIdentity.id}': {}
    }
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
              value: resolvedAzureAiProjectEndpoint
            }
            {
              name: 'AZURE_AI_MODEL_DEPLOYMENT_NAME'
              value: azureAiModelDeploymentName
            }
            {
              name: 'AZURE_AI_AGENT_ID'
              value: azureAiAgentId
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: agentappIdentity.properties.clientId
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        maxReplicas: 3
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
  name: guid(containerRegistry.id, agentappIdentity.id, 'AcrPull')
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') // AcrPull
    principalId: agentappIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

module azureAiRoleAssignment 'modules/azure-ai-role-assignment.bicep' = if (hasAzureAiAccount) {
  name: format('azureAiRoleAssignment-{0}', uniqueString(effectiveAzureAiAccountResourceId, agentappIdentity.id))
  scope: resourceGroup(azureAiSubscriptionId, azureAiResourceGroupName)
  params: {
    azureAiAccountName: resolvedAzureAiAccountName
    principalId: agentappIdentity.properties.principalId
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

@description('User-assigned managed identity resource ID for container image pulls.')
output agentappUserAssignedIdentityResourceId string = agentappIdentity.id

@description('User-assigned managed identity principal ID for container image pulls.')
output agentappUserAssignedIdentityPrincipalId string = agentappIdentity.properties.principalId

@description('Resolved Azure AI Foundry project endpoint.')
output AZURE_AI_PROJECT_ENDPOINT string = resolvedAzureAiProjectEndpoint

@description('Azure AI (Cognitive Services) account resource ID used by the deployment.')
output AZURE_AI_ACCOUNT_RESOURCE_ID string = effectiveAzureAiAccountResourceId

@description('Azure AI account name that was provisioned or supplied for this deployment.')
output AZURE_AI_ACCOUNT_NAME string = resolvedAzureAiAccountName

@description('Azure AI Foundry project name provisioned by this deployment (blank when reusing an external project).')
output AZURE_AI_PROJECT_NAME string = resolvedAzureAiProjectName
