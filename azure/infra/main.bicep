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

@description('Provision Azure OpenAI resources used by evaluator tooling.')
param enableAzureOpenAi bool = false

@description('Azure OpenAI account location. Must support the OpenAI service.')
param azureOpenAiLocation string = location

@description('Azure OpenAI account SKU name.')
param azureOpenAiSkuName string = 'S0'

@description('Azure OpenAI deployment name to create.')
param azureOpenAiDeploymentName string = 'gpt-4o-mini'

@description('Azure OpenAI base model name for the deployment.')
param azureOpenAiModelName string = 'gpt-4o-mini'

@description('Azure OpenAI model version for the deployment.')
param azureOpenAiModelVersion string = '2024-05-13'

@description('Azure OpenAI deployment SKU name.')
param azureOpenAiDeploymentSkuName string = 'Standard'

@description('Azure OpenAI deployment capacity.')
param azureOpenAiSkuCapacity int = 1

@description('Provision an Azure AI Foundry hub and project for this deployment.')
param enableAzureAiProject bool = false

@description('Display name applied to the Azure AI Foundry hub.')
param azureAiHubDisplayName string = 'Agent Hub'

@description('Display name applied to the Azure AI Foundry project.')
param azureAiProjectDisplayName string = 'Agent Project'

@description('Description applied to the Azure AI Foundry project.')
param azureAiProjectDescription string = 'Currency exchange agent project.'

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
var azureOpenAiAccountName = toLower(format('{0}aoai{1}', substring(alnumEnvSegment, 0, min(length(alnumEnvSegment), 8)), substring(uniqueSegment, 0, 6)))
var azureAiStorageAccountName = toLower(format('{0}aist{1}', substring(alnumEnvSegment, 0, min(length(alnumEnvSegment), 8)), substring(uniqueSegment, 0, 6)))
var azureAiKeyVaultName = toLower(format('{0}-aikv-{1}', substring(envSegment, 0, min(length(envSegment), 8)), substring(uniqueSegment, 0, 4)))
var azureAiHubName = toLower(format('{0}-aihub-{1}', substring(envSegment, 0, min(length(envSegment), 8)), substring(uniqueSegment, 0, 6)))
var azureAiProjectName = toLower(format('{0}-aiproj-{1}', substring(envSegment, 0, min(length(envSegment), 8)), substring(uniqueSegment, 0, 6)))
var normalizedAgentImage = toLower(agentappImage)
var normalizedRegistryServer = toLower(format('{0}.azurecr.io', containerRegistryName))
var useAcrRegistry = startsWith(normalizedAgentImage, normalizedRegistryServer)
var containerRegistries = useAcrRegistry ? [
  {
    server: containerRegistryLoginServer
    identity: 'system'
  }
] : []

var azureOpenAiSecretName = 'azure-openai-key'

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

resource azureOpenAiAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' = if (enableAzureOpenAi) {
  name: azureOpenAiAccountName
  location: azureOpenAiLocation
  kind: 'OpenAI'
  sku: {
    name: azureOpenAiSkuName
  }
  properties: {
    publicNetworkAccess: 'Enabled'
  }
  tags: {
    'azd-env-name': environmentName
    'azd-service': 'agentapp'
  }
}

resource azureOpenAiDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = if (enableAzureOpenAi) {
  name: azureOpenAiDeploymentName
  parent: azureOpenAiAccount
  sku: {
    name: azureOpenAiDeploymentSkuName
    capacity: azureOpenAiSkuCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: azureOpenAiModelName
      version: azureOpenAiModelVersion
    }
  }
}

resource azureAiStorage 'Microsoft.Storage/storageAccounts@2023-01-01' = if (enableAzureAiProject) {
  name: azureAiStorageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
  }
  tags: {
    'azd-env-name': environmentName
    'azd-service': 'agentapp'
  }
}

resource azureAiKeyVault 'Microsoft.KeyVault/vaults@2023-07-01' = if (enableAzureAiProject) {
  name: azureAiKeyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    publicNetworkAccess: 'Enabled'
    sku: {
      name: 'standard'
      family: 'A'
    }
  }
  tags: {
    'azd-env-name': environmentName
    'azd-service': 'agentapp'
  }
}

resource azureAiHub 'Microsoft.ProjectBabylon/accounts@2023-10-01-preview' = if (enableAzureAiProject) {
  name: azureAiHubName
  location: location
  kind: 'Hub'
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'Standard'
  }
  properties: {
    displayName: azureAiHubDisplayName
    description: azureAiProjectDescription
    publicNetworkAccess: 'Enabled'
    hubConfig: {
      storageAccountResourceId: azureAiStorage.id
      keyVaultResourceId: azureAiKeyVault.id
      applicationInsightsResourceId: appInsights.id
      containerRegistryResourceId: containerRegistry.id
    }
  }
  tags: {
    'azd-env-name': environmentName
    'azd-service': 'agentapp'
  }
}

resource azureAiProject 'Microsoft.ProjectBabylon/accounts@2023-10-01-preview' = if (enableAzureAiProject) {
  name: azureAiProjectName
  location: location
  kind: 'Project'
  sku: {
    name: 'Standard'
  }
  properties: {
    displayName: azureAiProjectDisplayName
    description: azureAiProjectDescription
    hubResourceId: azureAiHub.id
    publicNetworkAccess: 'Enabled'
  }
  tags: {
    'azd-env-name': environmentName
    'azd-service': 'agentapp'
  }
}

resource azureAiStorageRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableAzureAiProject) {
  name: guid(azureAiStorage.id, azureAiHub.id, 'StorageBlobDataContributor')
  scope: azureAiStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: azureAiHub!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource azureAiKeyVaultRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableAzureAiProject) {
  name: guid(azureAiKeyVault.id, azureAiHub.id, 'KeyVaultSecretsUser')
  scope: azureAiKeyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: azureAiHub!.identity.principalId
    principalType: 'ServicePrincipal'
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
      secrets: concat([
        {
          name: 'application-insights-connection-string'
          value: appInsights.properties.ConnectionString
        }
      ], enableAzureOpenAi ? [
        {
          name: azureOpenAiSecretName
          value: azureOpenAiAccount!.listKeys().key1
        }
      ] : [])
    }
    template: {
      containers: [
        {
          name: 'agentapp'
          image: agentappImage
          env: concat([
            {
              name: 'APPLICATION_INSIGHTS_CONNECTION_STRING'
              secretRef: 'application-insights-connection-string'
            }
            {
              name: 'AZURE_AI_PROJECT_ENDPOINT'
              value: enableAzureAiProject ? format('{0}/api/projects/{1}', azureAiProject!.properties.endpoint, azureAiProject!.name) : azureAiProjectEndpoint
            }
            {
              name: 'AZURE_AI_MODEL_DEPLOYMENT_NAME'
              value: azureAiModelDeploymentName
            }
            {
              name: 'AZURE_AI_AGENT_ID'
              value: azureAiAgentId
            }
          ], enableAzureOpenAi ? [
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: azureOpenAiAccount!.properties.endpoint
            }
            {
              name: 'AZURE_OPENAI_DEPLOYMENT'
              value: azureOpenAiDeploymentName
            }
            {
              name: 'AZURE_OPENAI_KEY'
              secretRef: azureOpenAiSecretName
            }
          ] : [])
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

@description('Azure OpenAI endpoint for evaluator tooling.')
output AZURE_OPENAI_ENDPOINT string = enableAzureOpenAi ? azureOpenAiAccount!.properties.endpoint : ''

@description('Azure OpenAI deployment name for evaluator tooling.')
output AZURE_OPENAI_DEPLOYMENT string = enableAzureOpenAi ? azureOpenAiDeploymentName : ''

@description('Azure OpenAI resource ID.')
output AZURE_OPENAI_RESOURCE_ID string = enableAzureOpenAi ? azureOpenAiAccount!.id : ''

@description('Azure AI Project endpoint to reference in downstream tooling.')
output AZURE_AI_PROJECT_ENDPOINT string = enableAzureAiProject ? format('{0}/api/projects/{1}', azureAiProject!.properties.endpoint, azureAiProject!.name) : azureAiProjectEndpoint

@description('Azure AI Project resource ID.')
output AZURE_AI_PROJECT_RESOURCE_ID string = enableAzureAiProject ? azureAiProject.id : ''

@description('Azure AI Hub resource ID.')
output AZURE_AI_HUB_RESOURCE_ID string = enableAzureAiProject ? azureAiHub.id : ''
