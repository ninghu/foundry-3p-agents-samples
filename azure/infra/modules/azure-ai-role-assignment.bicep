targetScope = 'resourceGroup'

@description('Name of the Azure AI (Cognitive Services) account inside the scoped resource group.')
param azureAiAccountName string

@description('Object ID of the principal to grant access (typically a managed identity).')
param principalId string

@description('Role definition ID to assign. Defaults to the Cognitive Services User role which grants data-plane access for Azure AI Foundry projects.')
param roleDefinitionId string = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')

resource azureAiAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: azureAiAccountName
}

resource azureAiRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(azureAiAccount.id, principalId, roleDefinitionId)
  scope: azureAiAccount
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleDefinitionId
  }
}
