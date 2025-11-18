targetScope = 'resourceGroup'

@description('Location for the Azure AI Foundry account and project.')
param location string = resourceGroup().location

@description('Tags applied to the Azure AI resources.')
param tags object = {}

@description('Name of the Azure AI (Cognitive Services) account to create.')
param accountName string

@description('Name of the Azure AI Foundry project to create.')
param projectName string

@description('Enable the Azure AI Agents capability host for hosted agents scenarios.')
param enableHostedAgents bool = true

resource aiAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  tags: tags
  sku: {
    name: 'S0'
  }
  kind: 'AIServices'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    networkAcls: {
      defaultAction: 'Allow'
      virtualNetworkRules: []
      ipRules: []
    }
    publicNetworkAccess: 'Enabled'
  }
}

resource aiAccountCapabilityHost 'Microsoft.CognitiveServices/accounts/capabilityHosts@2025-06-01' = if (enableHostedAgents) {
  name: 'agents'
  parent: aiAccount
  properties: {
    capabilityHostKind: 'Agents'
  }
}

resource aiProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: aiAccount
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: '${projectName} project'
    displayName: projectName
  }
}

@description('Resource ID for the created Azure AI account.')
output accountResourceId string = aiAccount.id

@description('Resource ID for the created Azure AI Foundry project.')
output projectResourceId string = aiProject.id

@description('Azure AI account name.')
output accountName string = aiAccount.name

@description('Azure AI project name.')
output projectName string = aiProject.name

@description('Azure AI project endpoint for API calls.')
output projectEndpoint string = aiProject.properties.endpoints['AI Foundry API']
